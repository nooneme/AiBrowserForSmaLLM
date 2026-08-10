# AI Browser for Small LLM

A web automation framework driven **only by local small LLMs**. It "looks" at the screen, reasons, and clicks just like a human — no DOM parsing, no injected API, no scraping rules. Everything is done through **screenshots + a vision model**.

## Key Features

### 1. Decide Based on the Current State + Short Operation History

Each loop starts from the **latest screenshot**, combined with a short list of the last few executed actions, and re-evaluates "where are we in the task, and what should be done next".

- Only the most recent N operations are kept (`tools/history_tools.py`, default 10) — avoids small models drifting or hallucinating over long contexts;
- Every step uses the newest page snapshot as input, so decisions always reflect **what the screen currently shows**;
- Naturally fault-tolerant: even if the previous step clicked wrong, the next step re-plans from the new screenshot.

### 2. Pure Vision Recognition + Operation

No HTML parsing, no injected scripts, no CSS selectors — the model simply "sees" the screenshot.

- Screenshot → model judges the next direction (`decide_next_step`, free text);
- Model emits an action JSON (`execute_action`): `click / open / input / scroll / task_complete`;
- A dedicated step fills in the action parameters (`resolve_*_params`): bounding box `bbox_2d`, text `text`, url `url`, scroll direction `scroll_dir`;
- Clicks are executed at the bbox center on the real browser, and the click position is drawn back onto the screenshot (`shots/`) for verification.

### 3. Runs on Local Small Models

Default integration with **llama.cpp**'s OpenAI-compatible endpoint (`/v1/chat/completions`, streaming SSE), loading a local multimodal GGUF model.

- Auto-starts `llama-server` (with a menu to pick model and toggle thinking) when the port is idle;
- Fully offline, no cloud API, no data leaves your machine;
- Prompting and error-handling (auto-retry on invalid JSON) are tuned specifically for "local small models + full vision".

### 4. Hard-to-Detect Real Edge

Automation runs against a real local Edge with an existing profile, plus stealth hardening:

- Persistent context reusing your login profile; `--disable-blink-features=AutomationControlled`;
- `init_script` hides `navigator.webdriver`, fakes the getter's `toString` to native, and removes leaked automation globals;
- `browser/detect_check.py` self-tests the remaining fingerprints.

## Tech Stack

- **Python 3.11+** — core language
- **Playwright (sync API)** — controls a local Edge browser (persistent context, reusable profile)
- **llama.cpp `llama-server`** — serves the local LLM via an OpenAI-compatible `/v1/chat/completions` endpoint
- **Vision-capable GGUF model** — e.g. **Qwen3.6-35B-A3B-Q4_K_M**; with optional `mmproj` vision projector
- **Pillow (PIL)** — screenshot decoding, coordinate normalization, drawing bbox/click markers
- **requests** — OpenAI-style streaming (SSE) calls to the local server

## Workflow

```
┌──────────┐  screenshot  ┌──────────────────────────┐
│  Edge    │─────────────▶│ 1. Decide next direction  │
│(Playwright)│            │    (free text)            │
└──────────┘              └─────────────┬────────────┘
        ▲                                ▼
        │                 ┌──────────────────────────┐
        │                 │ 2. Produce action JSON    │
        │                 │    click/open/input/      │
        │                 │    scroll/task_complete   │
        │                 └─────────────┬────────────┘
        │                               ▼
        │                 ┌──────────────────────────┐
        │                 │ 3. Fill action params     │
        │                 │    bbox/url/text/scroll   │
        │                 └─────────────┬────────────┘
        │                               ▼
        └────── 4. Execute & re-screenshot ◀─────────┘
```

Each loop is: **screenshot → decide direction → produce action → resolve params → execute**, driven by the current state plus a short operation history.

## Project Structure

```
.
├── main.py                      # Main loop: decide → action → resolve → execute
├── user_prompt.txt              # Task instruction, re-read each loop (editable at runtime)
├── browser/
│   ├── edge_browser.py          # Playwright Edge launcher; screenshot/click/type/scroll/tabs
│   └── detect_check.py          # Self-test for automation fingerprints (anti-detection)
├── llm/
│   └── llm.py                   # llama.cpp client, prompts, model/server auto-start
└── tools/
    ├── draw_tools.py            # Coordinate normalization, draw bbox/click markers
    └── history_tools.py         # Rolling operation history (keep last N actions)
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

> This project reuses a local Edge user-profile directory (the `PROFILE_DIR`
> constant in `browser/edge_browser.py`), so existing login sessions are
> inherited. Note: the profile directory must **not** be locked by a running
> Edge while the script starts.

### Local Model

1. Prepare a **multimodal** GGUF model (e.g. **Qwen3.6-35B-A3B-Q4_K_M**)
   in your models directory;
2. Make sure [llama.cpp](https://github.com/ggml-org/llama.cpp) is installed
   (`llama-server.exe`) — the code auto-detects and starts the server;
3. Constants such as `LLAMA_CPP_DIR`, `MODELS_DIR`, and `SERVER_URL` live at the
   bottom of `llm/llm.py`.

### Run

```bash
python main.py
```

On startup a menu lets you pick the model and toggle thinking. Put your task
instruction in `user_prompt.txt` (next to `main.py`) and set `URL` to the
starting page in `main.py`, then run. The prompt is re-read each loop, so you
can edit `user_prompt.txt` while the program is running and it takes effect
immediately.

## Extensible Actions

Five actions are built in, all located via pure vision:

| Action          | Effect                             | Filled-in params      |
|-----------------|------------------------------------|-----------------------|
| `click`         | Click an element                   | `bbox_2d`             |
| `input`         | Click an input box and type text   | `bbox_2d` + `text`    |
| `open`          | Open a URL                         | `url`                 |
| `scroll`        | Scroll the screen                  | `scroll_dir`          |
| `task_complete` | Task finished — break the loop     | `reason`              |

> `task_complete` ends the main loop (see `main.py`). New actions can be added
> by extending the prompts in `llm/llm.py` and the handlers in `main.py`.

## Browser Capabilities (`browser/edge_browser.py`)

- Reuse an existing Edge profile (persistent context, `channel="msedge"`);
- Screenshot (viewport or full page, PNG/JPEG);
- Click by normalized (0~1) or pixel coordinates;
- Type text with per-character delay and optional Enter to submit;
- Scroll by a fraction of the viewport height, over the mouse cursor position;
- Multi-tab support: `list_pages` / `switch_page`, and auto-follow newly opened
  tabs; navigate with `goto` / `go_back`;
- Anti-detection stealth init script and `detect_check.py` fingerprint check.
