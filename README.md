# Question Taskbar — Windows 10/11

Right-click near a question to read it with local OCR, ask a local model through
**Ollama**, and display the answer in a Windows taskbar button:

- Single choice: A → `1`, B → `2`, C → `3`, and so on through Z.
- Multiple selections: A, D, and F → `1,4,6`.
- Fill in the blank: the model's answer text, without a label or prefix.

This edition replaces the old volume signaling. It does not change your volume.
The default model is `qwen2.5:7b`, suitable for a computer with 16 GB or more
RAM. It requires no OpenAI account, API key, or per-question payment.
[Follow the local-model setup guide](LOCAL_MODEL_SETUP.md) for step-by-step
Windows instructions. A local model is separate from ChatGPT and its answers
can differ.

**Windows 11 hides taskbar labels by default.** Open **Settings →
Personalization → Taskbar → Taskbar behaviors → Combine taskbar buttons and
hide labels**, and select **Never**. If you use additional displays, set the
corresponding option for other taskbars as well. The program does not change
these settings for you. Windows controls button width, so a long answer may
be shortened with an ellipsis. The complete answer is also saved as UTF-8 text
to `%LOCALAPPDATA%\QuestionVolume\answer.txt`.

The program uses a minimized taskbar button with a changing caption. During
normal operation it opens no foreground application window, console, or answer
popup and does not take focus. While running, it consumes right-clicks so
context menus do not obscure questions. Double-click `stop.lnk` to restore
ordinary right-click behavior. Left-clicks, scrolling, and keyboard input
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
4. Extract the whole ZIP, then double-click **setup.cmd** inside the extracted
   folder. It runs the Python installer and leaves the terminal open so you
   can read its result. Alternatively, use Command Prompt or PowerShell:
   ```cmd
   py -3.14 install_windows.py
   ```
   This installs Python dependencies, selects the local model, checks
   Tesseract, Windows taskbar support, and the downloaded Ollama model,
   and creates `start.lnk` and `stop.lnk`. No API key or audio device is needed.
   Installation uses a visible terminal. No PowerShell script execution or
   execution-policy change is needed.
5. Enable taskbar labels as described above, then double-click `start.lnk`.
   Right-click near the question text, wait for the model response, and read
   the taskbar button's text. Double-click `stop.lnk` when finished. Starting
   twice does not create duplicate listeners.

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
indicator. Only the newest question's result is displayed; an older in-flight
answer cannot overwrite it. At most five screen captures can wait in the
queue. Rapid clicks that exceed the queue are skipped and recorded in the
log. OCR failure, uncertain selection, model abstention, or a solver error
clears the answer, including the local answer file. Stopping discards pending
work. There is no automatic start at Windows login.

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
| `suppress_right_click` | `true` | Consume right-clicks. Set `false` to retain context menus, which may obscure text. |
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

If the taskbar shows only an icon, enable **Never** for **Combine taskbar
buttons and hide labels**. This is a Windows setting; taskbar captions cannot
force text to appear while Windows hides labels. Long answers may be cut off
by Windows even with labels enabled. Open `answer.txt` to read the full text.
On Windows 10, the similar setting is **Combine taskbar buttons → Never**.

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
geometry, stop behavior, answer formatting, and taskbar adapter behavior.
A real Tesseract image test runs when its dependencies and a suitable font are
available. Solver calls and Windows desktop APIs are mocked in automated
checks. Windows global mouse hooks, taskbar labels and focus behavior, and
Credential Manager require validation on a real Windows desktop. The actual
Ollama model is checked separately with `--check-solver` on your PC.
