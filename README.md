# AI Browser for Small LLM

A web automation framework driven **only by local small LLMs**. It "looks" at the screen, reasons, and clicks just like a human — no DOM parsing, no API, no scraping rules. Everything is done through **screenshots + a vision model**.

## Key Features

### 1. Decide Based Only on the Current State — No History

Every iteration starts fresh from the **latest screenshot** and re-evaluates "where are we in the task, and what should be done next".

- No conversation history, no memory of past states — avoids small models going off the rails or hallucinating over long contexts;
- Every step takes the newest page snapshot as input, so decisions always reflect **what the screen currently shows**;
- Naturally fault-tolerant: even if the previous step clicked wrong, the next step re-plans based on the new screenshot.

### 2. Pure Vision Recognition + Operation

No HTML parsing, no injected scripts, no CSS selectors — the model simply "sees" the screenshot.

- Screenshot → model describes the page structure (`describe_page`);
- Model decides the next direction (`decide_next_action`);
- Model emits an action JSON (`execute_action`): `click / open / input / scroll`;
- A dedicated step fills in the action parameters: bounding box `bbox_2d`, text `text`, url `url`, scroll direction `scroll_dir`;
- Clicks are executed at the bbox center on the real browser, and the click position is drawn back onto the screenshot for verification.

### 3. Runs on Local Small Models

Default integration with **llama.cpp**'s OpenAI-compatible endpoint (`/v1/chat/completions`), loading a local multimodal GGUF model.

- Auto-starts `llama-server` and lets you pick a model from a menu at startup (supports `mmproj` vision projector);
- Fully offline, no cloud API, no data leaves your machine;
- Prompting and error-handling are tuned specifically for "local small models + full vision".

## Tech Stack

- **Python 3.11+** — core language
- **Playwright (sync API)** — controls a local Edge browser (persistent context, reusable profile)
- **llama.cpp `llama-server`** — serves the local LLM via an OpenAI-compatible `/v1/chat/completions` endpoint
- **Vision-capable GGUF model** — recommended: **Qwen3.6-35B-A3B-Q4_K_M**; with optional `mmproj` vision projector
- **Pillow (PIL)** — screenshot decoding, coordinate normalization, drawing bbox/click markers
- **requests** — OpenAI-style streaming (SSE) calls to the local server

## Workflow

```
┌──────────┐  screenshot  ┌─────────────────────┐
│  Edge    │─────────────▶│ 1. Describe page     │
│(Playwright)│            └──────────┬──────────┘
└──────────┘                         │
        ▲                            ▼
        │                 ┌─────────────────────┐
        │                 │ 2. Decide next step  │
        │                 └──────────┬──────────┘
        │                            ▼
        │                 ┌─────────────────────┐
        │                 │ 3. Produce action    │
        │                 │    click/open/input/ │
        │                 │    scroll            │
        │                 └──────────┬──────────┘
        │                            ▼
        │                 ┌─────────────────────┐
        │                 │ 4. Fill action params│
        │                 │    bbox/url/text/    │
        │                 │    scroll_dir        │
        │                 └──────────┬──────────┘
        │                            ▼
        └────── 5. Execute & re-screenshot ◀─────┘
```

Each loop is: **screenshot → describe → decide → execute**, entirely based on the current state with no dependency on history.

## Project Structure

```
.
├── main.py                  # Main loop: describe → decide → execute
├── browser/
│   └── edge_browser.py      # Playwright launcher for local Edge; screenshot/click/input/scroll
├── llm/
│   └── llm.py               # llama.cpp client, prompts, model/server startup
└── tools/
    └── draw_tools.py        # Coordinate normalization, draw bbox/click markers on screenshots
```

## Installation & Usage

### Dependencies

```bash
pip install -r requirements.txt
pip install playwright
```

On first use, let Playwright support your local Edge:

```bash
python -m playwright install msedge
```

> This project reuses your local Edge user-profile directory (the `PROFILE_DIR`
> constant in `browser/edge_browser.py`), so existing login sessions are inherited.

### Local Model

1. Prepare a **multimodal** GGUF model (recommended: **Qwen3.6-35B-A3B-Q4_K_M**)
   in your models directory;
2. Make sure [llama.cpp](https://github.com/ggml-org/llama.cpp) is installed
   (`llama-server.exe`) — the code auto-detects and starts the server;
3. Constants such as `LLAMA_CPP_DIR`, `MODELS_DIR`, and `SERVER_URL` live at the
   bottom of `llm/llm.py`.

### Run

```bash
python main.py
```

Set the `USER_PROMPT` at the top of `main.py` to your task and `URL` to the
starting page, then run.

## Extensible Actions

Four actions are built in, all located via pure vision:

| Action  | Effect                             | Filled-in params |
|---------|------------------------------------|------------------|
| `click` | Click an element                   | `bbox_2d`        |
| `input` | Click an input box and type text   | `bbox_2d` + `text`|
| `open`  | Open a URL                         | `url`            |
| `scroll`| Scroll the screen                  | `scroll_dir`     |

> To end the task, add a "task-complete" check in the main loop of `main.py`
> (currently an infinite `continue`, see the TODO comment).
