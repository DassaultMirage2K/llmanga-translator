"""Unit tests for the shared-conversation flow and 80% context rotation.

No network access: ``MangaTranslator.client`` is replaced with a fake that
returns scripted JSON responses and token-usage values.
"""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image as _PILImage

from app.service import MangaTranslator


REGIONS = [
    {
        "bbox": [0.1, 0.2, 0.5, 0.6],
        "original_text": "hello",
        "translated_text": "привет",
        "style": {"font_size": 0.05, "bold": False, "italic": False, "color": "#000000"},
    }
]


def make_image_file(tmp_path, name="page.jpg", color=(200, 30, 30)):
    buf = io.BytesIO()
    _PILImage.new("RGB", (16, 16), color).save(buf, format="JPEG")
    p = tmp_path / name
    p.write_bytes(buf.getvalue())
    return str(p)


class FakeLLM:
    """Stands in for translator.client; returns one scripted response per call."""

    def __init__(self):
        self.calls = []      # messages lists as sent to the LLM
        self.script = []     # (content, prompt_tokens, completion_tokens)

    def script_response(self, regions=REGIONS, summary="summary",
                        prompt_tokens=100, completion_tokens=50):
        payload = json.dumps({"text_regions": regions, "story_summary": summary})
        self.script.append((payload, prompt_tokens, completion_tokens))

    def script_failure(self):
        self.script.append(("this is not JSON at all", 100, 50))

    def attach(self, translator):
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._create))
        )
        translator.client = client

    def _create(self, model, messages, max_tokens=None, temperature=0.2, **kwargs):
        self.calls.append(messages)
        payload, p, c = self.script.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=payload), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=p, completion_tokens=c),
        )


def make_translator(tmp_path, context_window=10_000):
    t = MangaTranslator(context_window=context_window)
    fake = FakeLLM()
    fake.attach(t)
    return t, fake


def test_one_call_per_page_in_shared_conversation(tmp_path):
    t, fake = make_translator(tmp_path)
    pages = [make_image_file(tmp_path, f"{i}.jpg", color=(i * 60, 30, 30)) for i in range(3)]
    for n in (1, 2, 3):
        fake.script_response(summary=f"summary {n}")

    out = list(t.process_images(pages))

    assert len(out) == 3
    assert [o[0] for o in out] == pages
    assert [o[1] for o in out] == [REGIONS, REGIONS, REGIONS]
    assert [o[2] for o in out] == ["summary 1", "summary 2", "summary 3"]

    # exactly one LLM call per page; the conversation keeps growing
    assert len(fake.calls) == 3
    assert [m["role"] for m in fake.calls[0]] == ["system", "user"]
    assert [m["role"] for m in fake.calls[-1]] == [
        "system", "user", "assistant", "user", "assistant", "user"
    ]

    # each user message carries exactly one image + the page request
    last_user = fake.calls[-1][-1]
    parts = last_user["content"]
    assert sum(p.get("type") == "image_url" for p in parts) == 1
    assert any("Page 3" in p.get("text", "") for p in parts if p.get("type") == "text")


def test_rotation_at_80_percent_seeds_last_page_and_summary(tmp_path):
    t, fake = make_translator(tmp_path, context_window=1000)  # threshold = 800
    pages = [make_image_file(tmp_path, f"{i}.jpg", color=(i * 60, 30, 30)) for i in range(3)]

    fake.script_response(summary="story after p1", prompt_tokens=500, completion_tokens=100)   # 600 < 800
    fake.script_response(summary="story after p2", prompt_tokens=850, completion_tokens=50)    # 900 >= 800 -> rotate
    fake.script_response(summary="story after p3")

    out = list(t.process_images(pages))
    assert [o[2] for o in out] == ["story after p1", "story after p2", "story after p3"]

    # page 3 was sent into a FRESH conversation: system + seed user (last image)
    # + assistant summary + the new page
    third = fake.calls[2]
    assert [m["role"] for m in third] == ["system", "user", "assistant", "user"]

    seed_urls = [p["image_url"]["url"] for p in third[1]["content"] if p.get("type") == "image_url"]
    assert len(seed_urls) == 1
    seeded_bytes = base64.b64decode(seed_urls[0].split("base64,", 1)[1])
    assert seeded_bytes == Path(pages[1]).read_bytes(), "seed must be the last processed page"

    assert third[2]["content"].endswith("story after p2")


def test_failed_page_does_not_pollute_conversation(tmp_path):
    t, fake = make_translator(tmp_path)
    t.max_retries = 2
    t.retry_delay = 0
    pages = [make_image_file(tmp_path, f"{i}.jpg", color=(i * 60, 30, 30)) for i in range(3)]

    fake.script_response(summary="story p1")
    fake.script_failure()   # page 2: both attempts fail
    fake.script_failure()
    fake.script_response(summary="story p3")

    out = list(t.process_images(pages))
    assert out[0][1] == REGIONS and out[0][2] == "story p1"
    assert out[1][1] == [] and out[1][2] == "story p1"
    assert out[2][1] == REGIONS and out[2][2] == "story p3"

    # page 3 went into the ORIGINAL conversation (system + p1 pair + p3 user)
    last = fake.calls[-1]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "user"]
