# llmanga translator

Translates manga pages using a vision LLM (any OpenAI-compatible API). You upload the pages, it finds the text boxes on each page, translates them into your chosen language, and shows you the result with clickable boxes over the image. Translation works in the background so you don't need to keep the page open. Supports mobile viewports.

## screenshots

Main page
![main_page](/screenshots/main_page.png)
This page shows all the works saved. A work is a collection of images.

![work_page](/screenshots/work_page.png)
This page lets you upload and reorder images. It's also where you start the translation job.

![image](/screenshots/viewer.png)
Here is an image view. At the bottom, you can enter additional context if the LLM has trouble understanding the page.

![settings](/screenshots/settings.png)
Here is where you can change the translation language and system prompt. It also lets you choose what resolution images are resized to.

## env values

You can put these in a `.env` file at the project root — they get loaded automatically:

- `OPENAI_BASE_URL` (default `http://localhost:11434/v1`)
- `OPENAI_API_KEY` (default `sk-no-key-required`)
- `VISION_MODEL` (default `qwen3.8-27b`)
- `LLM_MAX_TOKENS` (default `8000`) - max tokens per answer
- `LLM_CONTEXT_WINDOW` (default `32768`) - model context
- `MANGA_DATA_DIR` (default `data`) 

## how to run it

On Windows: just double-click **`run_web.bat`**. 

Otherwise, from the project root:

```
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then open http://127.0.0.1:8000 in a browser.

You need Python >= 3.10 and [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.


## contributing

Mostly I will accept fixes and improvements. I made it for my personal use and at this point it's feature complete. I have no plans to expand it further.