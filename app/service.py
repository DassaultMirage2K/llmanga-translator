"""Manga translation service using a vision LLM.

Provides ``MangaTranslator`` which drives an OpenAI-compatible vision model to
detect, transcribe, and translate text regions on manga pages while carrying
story context forward across sequential images.

Pages are processed inside ONE shared conversation: each page is appended as a
user message (image + request) and the model returns both the extracted text
regions and a cumulative story summary in a single JSON response. When the
conversation reaches ~80% of the model's context window it is rotated: a new
conversation seeded with the last processed page image plus the LLM's own
story summary, then processing continues.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import re
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

import openai
from PIL import Image

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: JSON schema constraining the detection LLM's structured output.
TEXT_REGIONS_RESPONSE_FORMAT: Dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "manga_text_regions",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "text_regions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "bbox": {
                                "type": "array",
                                "items": {"type": "number", "minimum": 0, "maximum": 1},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            "original_text": {"type": "string"},
                            "translated_text": {"type": "string"},
                            "style": {
                                "type": "object",
                                "properties": {
                                    "font_size": {"type": "number"},
                                    "bold": {"type": "boolean"},
                                    "italic": {"type": "boolean"},
                                    "color": {"type": "string"},
                                },
                                "required": ["font_size", "bold", "italic", "color"],
                            },
                        },
                        "required": ["bbox", "original_text", "translated_text", "style"],
                    },
                },
                "story_summary": {"type": "string"},
            },
            "required": ["text_regions", "story_summary"],
        },
    },
}

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert manga translator and text detector. You translate this manga \
chapter page by page inside one ongoing conversation: every user message adds the \
next page image, and you keep track of the whole story as it unfolds.

## Your Task:
1. Identify ALL text in the newest page image (speech bubbles, thought bubbles, captions, sound effects, signs, etc.)
2. For each text element, determine its exact location using bounding boxes
3. Transcribe the original text
4. Translate the text to {target_language}
5. Update the story summary: describe the ENTIRE story so far (all previous \
pages plus this one) in 3-6 sentences, focusing on character interactions and \
important plot points. It must be cumulative, not just about the current page.

## Bounding Box Instructions (CRITICAL):
- Use normalized coordinates relative to the image dimensions
- Format: [x_min, y_min, x_max, y_max] where:
  * x_min = left edge position (0.0 to 1.0, where 0 is leftmost, 1 is rightmost)
  * y_min = top edge position (0.0 to 1.0, where 0 is topmost, 1 is bottommost)
  * x_max = right edge position (must be greater than x_min)
  * y_max = bottom edge position (must be greater than y_min)
- The bounding box must tightly enclose ALL visible text, including:
  * Text that spans multiple lines
  * Small text like sound effects
  * Text at angles (use the smallest axis-aligned box that contains all the text)
- Add small padding (2-3% of image size) around the text to ensure complete capture
- Be precise: inpainting will remove everything inside these boxes, so they must cover the text completely

## Output Format:
Return an object with two keys:
- "text_regions": array of objects. Each region has:
  - "bbox": [x_min, y_min, x_max, y_max] - normalized coordinates (each value between 0.0 and 1.0)
  - "original_text": "exact text from the image"
  - "translated_text": "your translation"
  - "style": {{
      "font_size": 0.05,  // relative to image height (0.05 = 5% of image height)
      "bold": false,
      "italic": false,
      "color": "#000000"  // hex color if visible, otherwise ""
    }}
- "story_summary": "cumulative description of the whole story up to and including this page (3-6 sentences)"

Your response is constrained by a JSON schema; emit only the fields it defines.

## Context for Consistency:
Chapter context: {context}
Glossary: {glossary}
Use the glossary terms when translating. Maintain consistent character names and terminology.

## Important:
- Your response is validated against a JSON schema; include only the defined fields
- If no text is found on this page, set "text_regions" to an empty array [] (still update story_summary)
- Double-check all bounding boxes are accurate
- Ensure all text is captured, including small or faint text
"""

_PAGE_PROMPT_TEMPLATE = (
    "Page {page_no} of this manga chapter. Extract all text regions from the image "
    "and update the cumulative story summary."
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _encode_image(image_path: str) -> str:
    """Read image file and return a data URL (``data:image/...;base64,...``)."""
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or mime_type == "image/jpg":
        mime_type = "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def _extract_json_from_text(text: str) -> Optional[str]:
    """Extract a JSON array or object embedded in arbitrary text."""
    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class MangaTranslator:
    """Translate manga pages using an OpenAI-compatible vision LLM.

    The translator detects text regions (bounding box + transcription),
    translates them, and generates a running story summary that is carried
    forward as context for subsequent pages.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        target_language: str = "English",
        extra_system_prompt: str = "",
        resize_max_side: Optional[int] = None,
        max_retries: int = 5,
        retry_delay: float = 1.0,
        max_tokens: Optional[int] = None,
        context_window: Optional[int] = None,
    ):
        self.client = openai.OpenAI(
            base_url=base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
            api_key=api_key or os.getenv("OPENAI_API_KEY", "sk-no-key-required"),
        )
        self.model = model or os.getenv("VISION_MODEL", "llava")
        self.target_language = target_language
        self.extra_system_prompt = extra_system_prompt
        self.resize_max_side = resize_max_side
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # Dense manga pages easily exceed 2000 tokens of JSON. Default is generous;
        # override via LLM_MAX_TOKENS env or the max_tokens argument.
        self.max_tokens = int(
            max_tokens if max_tokens is not None else os.getenv("LLM_MAX_TOKENS", "8000")
        )
        # Model context window (tokens). The shared conversation is rotated once
        # it reaches rotate_threshold of this value. Override via LLM_CONTEXT_WINDOW.
        self.context_window = int(
            context_window if context_window is not None else os.getenv("LLM_CONTEXT_WINDOW", "32768")
        )
        self.rotate_threshold = 0.8

        # Per-chapter state, (re)initialised by begin_chapter().
        self._conversation: List[Dict] = []
        self._story_summary = ""
        self._last_image_path: Optional[str] = None
        self._page_no = 0
        self._last_context_tokens: Optional[int] = None
        self._usage_warned = False

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def begin_chapter(
        self,
        initial_context: str = "",
        glossary: Optional[Dict] = None,
    ) -> None:
        """(Re)start the shared conversation for a chapter."""
        self._initial_context = (initial_context or "").strip()
        self._glossary = glossary
        self._conversation = [
            {
                "role": "system",
                "content": self._build_system_prompt(self._initial_context, glossary),
            }
        ]
        self._story_summary = ""
        self._last_image_path: Optional[str] = None
        self._page_no = 0
        self._last_context_tokens: Optional[int] = None
        self._usage_warned = False

    def process_images(
        self,
        image_paths: List[str],
        initial_context: str = "",
        glossary: Optional[Dict] = None,
    ) -> Generator[Tuple[str, List[Dict], str], None, None]:
        """Process images sequentially in ONE shared conversation.

        Yields ``(image_path, text_regions, context_after_page)`` for each image;
        ``context_after_page`` is the cumulative story summary up to that page.
        On failure the yielded ``text_regions`` is an empty list and context is
        unchanged so callers can continue.
        """
        self.begin_chapter(initial_context=initial_context, glossary=glossary)

        for image_path in image_paths:
            try:
                result = self.process_page(image_path)
                yield image_path, result["text_regions"], result["context_after"]
            except Exception as e:
                logger.warning("Error processing %s: %s", image_path, e)
                yield image_path, [], self._story_summary

    def process_page(self, image_path: str, user_context: str = "") -> Dict[str, Any]:
        """Translate one page inside the shared conversation (blocking).

        Appends the page to the ongoing conversation and makes a single LLM call
        that returns both text regions and the cumulative story summary. Returns
        ``{"text_regions": [...], "context_after": "<story summary>"}``.
        On failure after all retries nothing is appended to the conversation.
        """
        self._page_no += 1
        user_msg = self._page_user_message(image_path, self._page_no, user_context)
        messages = self._conversation + [user_msg]

        regions, summary, raw = self.call_llm_with_retry(messages)

        # Commit only after success so a failed page never pollutes the context.
        self._conversation.extend([user_msg, {"role": "assistant", "content": raw}])
        if summary:
            self._story_summary = summary
        self._last_image_path = image_path
        self._maybe_rotate()
        return {"text_regions": regions, "context_after": self._story_summary}

    def call_llm_with_retry(
        self,
        messages: List[Dict],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> Tuple[List[Dict], str, str]:
        """Call the LLM with retries; returns ``(regions, story_summary, raw)``."""
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                content, usage_total = self._call_llm(
                    messages, max_tokens, temperature,
                    response_format=TEXT_REGIONS_RESPONSE_FORMAT,
                )
                regions, summary = self.parse_llm_response(content)
                if not regions and attempt < self.max_retries - 1:
                    raise ValueError("Empty text_regions from LLM")
                self._last_context_tokens = usage_total
                return regions, summary, content

            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                logger.warning(
                    "Attempt %d/%d failed: %s", attempt + 1, self.max_retries, e
                )
                if attempt < self.max_retries - 1:
                    messages = self._add_json_emphasis(messages)
                    time.sleep(self.retry_delay * (2 ** attempt))

            except Exception as e:
                last_error = e
                logger.warning(
                    "Attempt %d/%d API error: %s", attempt + 1, self.max_retries, e
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise

        raise ValueError(
            f"Failed to get valid JSON after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def _maybe_rotate(self) -> None:
        """Rotate the conversation once it reaches ~80% of the context window.

        The new conversation is seeded with the system prompt, the last
        processed page image and the LLM's own story summary, so the model
        keeps its place in the story without re-reading everything.
        """
        total = self._last_context_tokens
        limit = int(self.context_window * self.rotate_threshold)
        if total is None:
            if not self._usage_warned:
                logger.warning(
                    "LLM did not report token usage; context rotation disabled"
                )
                self._usage_warned = True
            return
        if total < limit or not self._last_image_path:
            return

        logger.info(
            "Context reached %d tokens (>= %d); rotating with last page + story summary",
            total, limit,
        )
        context_parts = [p for p in (self._initial_context, self._story_summary) if p]
        seed_user: List[Dict] = [
            {
                "type": "text",
                "text": "This is the most recent page we translated in this chapter.",
            },
            {
                "type": "image_url",
                "image_url": {"url": self._encode_for_llm(self._last_image_path)},
            },
        ]
        summary_text = self._story_summary or "(no story summary available yet)"
        self._conversation = [
            {
                "role": "system",
                "content": self._build_system_prompt(
                    "\n\n".join(context_parts), self._glossary
                ),
            },
            {"role": "user", "content": seed_user},
            {"role": "assistant", "content": f"Story so far: {summary_text}"},
        ]

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self, context: str = "", glossary: Optional[Dict] = None
    ) -> str:
        """Build the system prompt for (a rotated) conversation."""
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            target_language=self.target_language,
            context=context or "(none)",
            glossary=json.dumps(glossary, ensure_ascii=False) if glossary else "None",
        )

        # Prepend user-supplied extra instructions without replacing the spec.
        if self.extra_system_prompt.strip():
            system_prompt = self.extra_system_prompt.strip() + "\n\n" + system_prompt
        return system_prompt

    def _page_user_message(
        self, image_path: str, page_no: int, user_context: str = ""
    ) -> Dict:
        """Build the per-page user message (image + extraction request)."""
        text = _PAGE_PROMPT_TEMPLATE.format(page_no=page_no)
        if user_context and user_context.strip():
            text += f"\nAdditional context from user: {user_context.strip()}"
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": self._encode_for_llm(image_path)},
                },
            ],
        }

    # ------------------------------------------------------------------
    # LLM interaction (low-level)
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        messages: List[Dict],
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        response_format: Optional[Dict] = None,
    ) -> str:
        """Send a chat completion request; returns ``(content, total_tokens_or_None)``."""
        if max_tokens is None:
            max_tokens = self.max_tokens

        kwargs: Dict[str, Any] = {}
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

        content = response.choices[0].message.content
        if not content or not content.strip():
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                raise ValueError(
                    f"LLM output truncated at token limit (max_tokens={max_tokens}). "
                    f"Increase max_tokens (env LLM_MAX_TOKENS)."
                )
            raise ValueError(
                f"LLM returned empty/null content (finish_reason={finish_reason}). "
                f"The backend may not support structured output for this model."
            )

        usage_total: Optional[int] = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            try:
                usage_total = int(usage.prompt_tokens or 0) + int(usage.completion_tokens or 0)
            except (TypeError, ValueError):
                usage_total = None
        return content, usage_total

    # ------------------------------------------------------------------
    # Response parsing & normalization
    # ------------------------------------------------------------------

    def parse_llm_response(self, raw_response: str) -> Tuple[List[Dict], str]:
        """Parse an LLM response into ``(text_regions, story_summary)``.

        Raises ``ValueError`` if the response cannot be interpreted as regions.
        """
        data = self._extract_json(raw_response)
        regions = self._normalize_regions(data)
        self._fix_bboxes(regions)

        summary = ""
        if isinstance(data, dict):
            value = data.get("story_summary")
            if isinstance(value, str):
                summary = value.strip()
        return regions, summary

    def _extract_json(self, raw: str) -> Any:
        """Parse JSON from the raw LLM response (direct or embedded)."""
        if not raw or not raw.strip():
            raise ValueError("LLM returned an empty response")

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        json_str = _extract_json_from_text(raw)
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Could not parse JSON from response: {e}")

        snippet = raw[:200].replace("\n", " ")
        raise ValueError(
            f"No JSON found in response. Got non-JSON text ({len(raw)} chars): {snippet!r}"
        )

    def _normalize_regions(self, data: Any) -> List[Dict]:
        """Coerce parsed JSON into a flat list of region dicts."""
        if isinstance(data, dict):
            if "text_regions" in data:
                regions = data["text_regions"]
            elif "regions" in data:
                regions = data["regions"]
            elif "text" in data or "bbox" in data:
                regions = [data]  # single region object
            else:
                regions = list(data.values()) if data else []
        elif isinstance(data, list):
            regions = data
        else:
            raise ValueError(f"Unexpected response type: {type(data)}")

        if not isinstance(regions, list):
            raise ValueError(f"Expected list of regions, got {type(regions)}")
        return regions

    def _fix_bboxes(self, regions: List[Dict]) -> None:
        """Validate and repair bounding boxes in-place."""
        for region in regions:
            if "bbox" not in region:
                continue
            bbox = region["bbox"]
            if not (isinstance(bbox, list) and len(bbox) == 4):
                logger.warning("Invalid bbox format %s, removing", bbox)
                del region["bbox"]
                continue

            # Clamp to [0, 1]
            if not all(0 <= v <= 1 for v in bbox):
                logger.warning("bbox values out of range %s, clamping", bbox)
                bbox = [max(0, min(1, v)) for v in bbox]

            # Ensure x_max > x_min and y_max > y_min
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                logger.warning("Inverted bbox %s, swapping", bbox)
                bbox = [
                    min(bbox[0], bbox[2]), max(bbox[0], bbox[2]),
                    min(bbox[1], bbox[3]), max(bbox[1], bbox[3]),
                ]

            region["bbox"] = bbox

    # ------------------------------------------------------------------
    # Image encoding
    # ------------------------------------------------------------------

    def _encode_for_llm(self, image_path: str) -> str:
        """Encode an image as a data URL for the LLM payload.

        When ``resize_max_side`` is set the image is downscaled (LANCZOS, JPEG);
        otherwise raw file bytes are sent unchanged.
        """
        if not self.resize_max_side:
            return _encode_image(image_path)

        img = Image.open(image_path)
        w, h = img.size
        px = self.resize_max_side
        if w >= h:
            new_w, new_h = px, max(1, round(h * px / w))
        else:
            new_w, new_h = max(1, round(w * px / h)), px

        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _add_json_emphasis(self, messages: List[Dict]) -> List[Dict]:
        """Append a JSON-only reminder to the system message (for retries)."""
        new_messages = list(messages)
        for i, msg in enumerate(new_messages):
            if msg["role"] == "system":
                content = (
                    msg["content"]
                    + "\n\nCRITICAL: Respond with ONLY a valid JSON object of the form"
                    ' {"text_regions": [...]}. No markdown, no code fences, no prose.'
                )
                new_messages[i] = {"role": "system", "content": content}
        return new_messages
