# Question Taskbar — Windows 11

Right-click near a question to read it with local OCR, ask a local model through
**Ollama**, and display the answer in an empty part of the Windows taskbar:

- Single choice: A → `1`, B → `2`, C → `3`, and so on through Z → `26`.
- Multiple selections: any subset of the visible choices, listed in choice
  order and separated by commas. For example, B, E, and Z → `2,5,26`;
  A, D, and F → `1,4,6`. These are examples, not a fixed list of supported
  selections.
- Fill in the blank: the model's answer text, without a label or prefix.

This edition replaces the old volume signaling. It does not change your volume.
The default model is `qwen2.5:7b`, suitable for a computer with 16 GB or more
RAM. It requires no OpenAI account, API key, or per-question payment.
[Follow the local-model setup guide](LOCAL_MODEL_SETUP.md) for step-by-step
Windows instructions. A local model is separate from ChatGPT and its answers
can differ.

The answer is a small, click-through text overlay, with no taskbar app button
or new pinned icon. It measures empty taskbar space and leaves a buffer around
Start, Search, weather/widgets, pinned and running applications, and the
system tray and clock. It does not move those controls or change Windows
settings. Long answers scroll within the available gap. The complete answer
is also saved as UTF-8 text to
`%LOCALAPPDATA%\QuestionVolume\answer.txt`.

During normal operation it opens no foreground application window, console,
or answer popup and does not take focus. Mouse clicks pass through the answer
overlay. While running, it consumes question-area right-clicks so
context menus do not obscure questions. Taskbar right-clicks pass through,
preserving pinned-app menus, Search, widgets, and tray controls. Double-click
`stop.lnk` to restore ordinary right-click behavior everywhere. Left-clicks, scrolling, and keyboard input
continue to work normally.

## Install on your Windows computer

1. Install **Python 3.14.8 (standard 64-bit build)** from
   <https://www.python.org/downloads/release/python-3148/>, including the
   Python launcher (`py`). See [Python compatibility](PYTHON_COMPATIBILITY.md).
2. Install **Tesseract OCR**, including English language data. The Tesseract
   project lists Windows installers at
   <https://tesseract-ocr.github.io/tessdoc/Installation.html>.
   Alternatively, use PowerShell:
   ```powershell
   winget install --exact --id UB-Mannheim.TesseractOCR
   ```
3. Install **Ollama for Windows** from <https://ollama.com/download/windows>.
   Open a new PowerShell window after installation, then download the model:
   ```powershell
   ollama pull qwen2.5:7b
   ```
   The model download is about 4.7 GB. Keep Ollama running; its desktop app
   runs a local server in the background.
4. Download the [Windows ZIP](https://github.com/reverb000/idk/archive/refs/heads/codex/windows-question-taskbar.zip)
   and extract it, then double-click **setup.cmd** inside the extracted folder.
   It runs the Python installer and leaves the terminal open so you
   can read its result. Alternatively, use Command Prompt or PowerShell:
   ```cmd
   py -3.14 install_windows.py
   ```
   This installs Python dependencies, selects the local model, checks
   Tesseract, Windows taskbar support, and the downloaded Ollama model,
   and creates `start.lnk` and `stop.lnk`. No API key or audio device is needed.
   Installation uses a visible terminal. No PowerShell script execution or
   execution-policy change is needed.
5. Double-click `start.lnk`.
   Right-click near the question text, wait for the model response, and read
   the text in an empty part of the taskbar. Double-click `stop.lnk` when
   finished. Starting twice does not create duplicate listeners.

`start.pyw` and `stop.pyw` also select this folder's installed Python environment,
even if Windows associates Python files with another Python installation.

If you prefer to install manually, run these commands in an interactive
PowerShell prompt. They do not run a PowerShell script:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m question_volume --configure-backend ollama --model qwen2.5:7b
.\.venv\Scripts\python.exe -m question_volume --check
Start-Process -FilePath .\.venv\Scripts\pythonw.exe -ArgumentList '"start.pyw"' -WorkingDirectory $PWD
```

To stop without a shortcut, run:

```powershell
.\.venv\Scripts\python.exe -m question_volume --stop
```

The Python module is still named `question_volume` for compatibility with
existing commands. If you already use Python 3.12,
`setup.ps1 -PythonVersion 3.12` keeps that workflow, or use
`py -3.12 install_windows.py`. To move to 3.14, extract the updated ZIP into a
fresh folder. Setup refuses to reuse a virtual environment from a different
Python minor version, which prevents loading incompatible native packages.

To test a real local model response without capturing your screen or updating
the taskbar:

```powershell
.\.venv\Scripts\python.exe -m question_volume --check-solver
```

This asks a small known-answer question. Passing it verifies connectivity and
response parsing, not accuracy on every question. CPU-only responses can take
time, especially while the model first loads.

### Optional OpenAI backend

If you prefer the OpenAI API, obtain a key at
<https://platform.openai.com/api-keys> with access to `gpt-4.1-mini` and API
billing enabled, then run:

```powershell
py -3.14 install_windows.py --backend openai --model gpt-4.1-mini
```

This prompts for your key with hidden input and stores it in **Windows
Credential Manager**. API billing is separate from a ChatGPT subscription.
`OPENAI_API_KEY` in the process environment overrides the stored key. To
replace the stored key, run `--set-api-key`. You can remove it from Windows
Credential Manager under the `question-volume` entry. Do not put keys in
the configuration file or source code. To switch back, run
`py -3.14 install_windows.py --backend ollama --model qwen2.5:7b`.

## How it behaves

Each right-button press queues a small capture around that cursor position.
OCR selects the closest question. Lettered choices should be arranged
vertically, using sequential labels such as `A.`, `B)`, or `(C)`. Keep the
whole question and its options visible within the capture area. For a fill-in
question, keep the complete prompt and blank visible. OCR supports wrapped
text and multiple nearby questions; sparse-text recognition retries difficult
layouts with an enlarged block-text pass.

With the default Ollama backend, recognized question and answer-choice text
is sent to the local server at `http://127.0.0.1:11434`. Solving uses the
downloaded model on your computer; no cloud key is used. Once installed and
downloaded, the local workflow can run offline. Installation, model downloads,
and software updates need internet access.

Screenshots remain in memory; they are not uploaded or saved. The latest
answer is saved locally in `%LOCALAPPDATA%\QuestionVolume\answer.txt`. Logs
record processing status. If you select the optional OpenAI backend, recognized text is sent
to OpenAI with `store=false`; OpenAI's applicable API data policies still apply.

A new accepted click clears the previous answer and displays a processing
indicator when a suitable taskbar gap is available. Only the newest question's
result is displayed; an older in-flight answer cannot overwrite it. At most
five screen captures can wait in the
queue. Rapid clicks that exceed the queue are skipped and recorded in the
log. OCR failure, uncertain selection, model abstention, or a solver error
clears the answer, including the local answer file. Stopping discards pending
work. There is no automatic start at Windows login.

Taskbar placement uses Windows UI Automation and native child-window bounds
to measure occupied areas. It refreshes about every 200 milliseconds and
moves or hides the overlay as the layout changes. It draws only when it can
identify an empty horizontal gap at least 56 device-independent pixels wide,
after leaving a margin around neighboring controls. If no readable gap exists,
the measurements are incomplete or stale, or a full-screen application is
active, the overlay stays hidden. The answer file remains available even when
nothing can be drawn safely.

The overlay also stays hidden whenever **Automatically hide the taskbar** is
enabled, including while the taskbar is visible on hover. Windows does not
provide enough reliable information about its auto-hide animation and clipping
to place the overlay safely. Leave this option off under **Settings →
Personalization → Taskbar → Taskbar behaviors** if you want the answer overlay.
The program does not change it automatically.

This is a standalone overlay; it does not resize or reparent the Windows
taskbar. Windows does not provide a reserved area for it, so layout changes
can happen between measurements. Click-through behavior ensures the overlay
does not intercept mouse input, even during a layout change.

OCR and model answers can be wrong, particularly for equations, diagrams,
clipped text, and unusual layouts. This implementation sends text only, so
diagram-dependent questions cannot be reliably answered. A fill-in answer is
the model's returned text, not a guarantee of the correct wording.

## Configuration and troubleshooting

Copy `config.example.json` to `config.json` to customize settings. Restart
the program after changes. `config.json` is ignored by Git.
If you reuse an older configuration with a 30-second timeout, increase
`api_timeout_seconds` to `180` for local inference. Legacy `direction` and
`step_percent` settings are accepted but ignored by this taskbar edition.

| Setting | Default | Effect |
| --- | --- | --- |
| `backend` | `ollama` | `ollama` for local inference, or `openai` for the optional cloud API. |
| `model` | `qwen2.5:7b` | Downloaded Ollama model name; for OpenAI, a model supporting Responses structured outputs. |
| `capture_width`, `capture_height` | `1400`, `1000` | Capture area in physical pixels, clamped to the cursor's monitor. |
| `suppress_right_click` | `true` | Consume question-area right-clicks. Taskbar events always pass through. Set `false` to retain other context menus. |
| `cooldown_seconds` | `0` | Optional minimum interval between triggers. |
| `tesseract_cmd` | `null` | Auto-detect Tesseract; override with its full executable path. |
| `language` | `eng` | Installed Tesseract language code. |
| `min_confidence` | `40` | Minimum OCR word confidence. |
| `api_timeout_seconds` | `180` | Local/cloud request timeout; accepts 5–600 seconds. |

For a custom OCR path, use JSON escaping:

```json
{"tesseract_cmd": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"}
```

Check `%LOCALAPPDATA%\QuestionVolume\app.log` if nothing happens. Typical
causes are missing Tesseract/language data, no readable complete question,
Ollama not running, or a model that has not been downloaded. `--check` verifies
local prerequisites without displaying an answer; `--check-solver` also asks a
known-answer question. A successful real right-click is the end-to-end check.
See [local-model troubleshooting](LOCAL_MODEL_SETUP.md#troubleshooting).

For a **missing Python module** or a silent startup failure, double-click
**diagnose.cmd** in the extracted program folder. It keeps a terminal open,
prints the Python interpreter and missing module names, and checks local
prerequisites without capturing the screen or asking the model a question.
If a Python dependency is missing, stop the app and double-click **repair.cmd**
to reinstall the required Python packages in this folder's `.venv` and rerun
diagnostics. Repair preserves `config.json` and the downloaded Ollama model.
If `.venv` does not exist, run **setup.cmd** first. Early launch failures are
recorded in `%LOCALAPPDATA%\QuestionVolume\startup.log`; normal application
status and errors are in `app.log`.

If an answer is saved but nothing appears on the taskbar, first leave
full-screen mode and ensure **Automatically hide the taskbar** is off. The
overlay remains hidden while that option is enabled, even when the taskbar
appears on hover. A crowded taskbar or unreliable Windows layout measurements
also cause the overlay to hide intentionally;
it will not draw over Search, weather, app buttons, or the tray to make room.
Open `%LOCALAPPDATA%\QuestionVolume\answer.txt` to read the complete answer.
Long text scrolls inside the available gap, so it may take time to see it all.
No taskbar label or grouping settings need to be changed.

For the OpenAI backend, also check API credentials and billing. `--check`
does not authenticate an OpenAI key remotely; `--check-solver` makes an API
request and incurs normal usage charges.

This tool cannot capture Windows secure-desktop screens such as UAC prompts.
Multi-monitor capture uses the clicked monitor and accounts for DPI scaling.
It must run on the Windows desktop where the questions appear, not in this
Linux cloud environment.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover OCR layout selection, solver responses and failures, capture
geometry, stop behavior, all-letter and multiselect answer formatting, and
taskbar adapter behavior, including synthetic occupied-area geometry.
A real Tesseract image test runs when its dependencies and a suitable font are
available. Solver calls and Windows desktop APIs are mocked in automated
checks. Native UI Automation, overlay placement and scrolling on a live
Windows taskbar, global mouse hooks, focus behavior, and Credential Manager
require validation on a real Windows desktop. These Linux checks do not
establish native Windows placement behavior. The actual Ollama model is
checked separately with `--check-solver` on your PC.
