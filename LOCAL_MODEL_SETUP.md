# Run Question Taskbar with a local AI model on Windows

For your computer with **16 GB or more RAM**, start with **Ollama** and
**Qwen 2.5 7B** (`qwen2.5:7b`). You do not need an OpenAI API key or a paid
ChatGPT subscription. The model runs on your computer. A compatible GPU can
make responses faster; it can also run on the CPU, more slowly.

Use the updated Question Taskbar edition. It shows answers in a Windows
taskbar button instead of changing the volume. The earlier OpenAI-only
edition will not use Ollama just because you install it.

## 1. Install Ollama and download the model

1. Download and install Ollama for Windows from
   <https://ollama.com/download/windows>.
2. Open **PowerShell** from the Start menu. If it was already open during
   installation, close it and open a new one.
3. Run:
   ```powershell
   ollama pull qwen2.5:7b
   ```
   Wait for the download to finish. The model is about **4.7 GB**, so allow
   several GB of free disk space in addition to the application and Python
   dependencies. Your first download needs internet access.
4. Confirm that the model is installed:
   ```powershell
   ollama list
   ```
   You should see `qwen2.5:7b` in the list.

For an optional first test, run:

```powershell
ollama run qwen2.5:7b
```

Type a simple question and press Enter. Type `/bye` to leave the model chat.
Leave the Ollama desktop app running; Question Taskbar talks to its background
server. You do not need to keep the interactive model chat open.

## 2. Install the OCR program's requirements

If you already installed these for the previous version, you can reuse them:

1. Install **Python 3.14.8 (standard 64-bit build)** from
   <https://www.python.org/downloads/release/python-3148/>. Include the Python
   launcher (`py`) in the installer options. If you already use Python 3.12,
   you can retain it by running `py -3.12 install_windows.py`; see
   [Python compatibility](PYTHON_COMPATIBILITY.md).
2. Install **Tesseract OCR** with its English language data. The Tesseract
   project lists Windows installers at
   <https://tesseract-ocr.github.io/tessdoc/Installation.html>.
   Alternatively, run this in PowerShell:
   ```powershell
   winget install --exact --id UB-Mannheim.TesseractOCR
   ```
3. Extract the whole updated ZIP into a folder you can keep, such as
   `Documents\QuestionTaskbar`. Open that folder in File Explorer.
4. Double-click **setup.cmd** in the extracted folder. Alternatively, open
   PowerShell in this folder by typing `powershell` into File Explorer's
   address bar, pressing Enter, and running:
   ```cmd
   py -3.14 install_windows.py
   ```
   This installs the Python packages, configures `qwen2.5:7b`, checks OCR,
   Windows taskbar support, and Ollama, and creates **start.lnk** and
   **stop.lnk**. It does not ask for an OpenAI API key or require an audio
   device. This uses Python rather than a PowerShell script, so `setup.ps1`
   does not need to be enabled. The installer window stays open to show
   errors. When moving from Python 3.12 to 3.14, use a fresh extracted folder
   so the existing virtual environment and your files remain intact.
5. Test a real model response:
   ```powershell
   .\.venv\Scripts\python.exe -m question_volume --check-solver
   ```
   This checks a known-answer question without capturing your screen or
   displaying an answer on the taskbar. A pass confirms that the local solver
   responds; it does not guarantee the answers to other questions are correct.

If PowerShell blocked `setup.ps1`, use `setup.cmd` or the Python command above.
The [README](README.md#install-on-your-windows-computer) also contains manual
commands. A fresh installation uses the local model by default. If you have
an existing `config.json`, ensure it includes `"backend": "ollama"` and
`"model": "qwen2.5:7b"`. If that older configuration uses a 30-second timeout,
set `"api_timeout_seconds": 180` to allow a local model more time. The Python
module remains `question_volume` and the local data folder remains
`QuestionVolume` for compatibility.

## 3. Enable taskbar text on Windows 11

1. Open **Settings → Personalization → Taskbar → Taskbar behaviors**.
2. Set **Combine taskbar buttons and hide labels** to **Never**.
3. If you use other displays, set the corresponding combining option for
   those taskbars to **Never** as well.

Windows otherwise hides the answer text and shows only an icon. The program
does not change taskbar settings automatically. On Windows 10, the similar
setting is **Combine taskbar buttons → Never**. If your Windows 11 version
does not offer this setting, install current Windows updates first.

Windows may shorten a long taskbar caption. The complete answer is also saved
as UTF-8 text at `%LOCALAPPDATA%\QuestionVolume\answer.txt`. You can paste this
path into File Explorer's address bar to read it. This file is local and may
contain the most recent answer; do not share it if that text is private.

## 4. Start and use it

1. Keep Ollama running. Its Windows desktop app serves models in the background.
2. Double-click **start.lnk**. Question Taskbar creates a minimized taskbar
   button without opening a foreground window or taking focus.
3. Show the complete question. For multiple choice or multiple selections,
   include all lettered answer options. For a fill-in question, include the
   complete prompt and blank. Move the cursor near the question and
   **right-click once**.
4. Wait for OCR and the model to finish. The first response can take longer
   because the model needs to load. Read the taskbar button: **A → `1`**, a
   multiselect such as **A, D, F → `1,4,6`**, or the returned fill-in text.
   A new accepted click clears the previous answer and shows a processing
   indicator. Only the newest question's result appears; errors clear the
   answer so an old result is not mistaken for a new one.
5. Double-click **stop.lnk** when finished. While Question Taskbar runs, it
   consumes right-clicks; stopping restores normal right-click menus.

The setup terminal and Ollama installation are visible. Routine Question
Taskbar operation opens no foreground answer window or popup. Ollama may
have its own tray icon. After installing and downloading the model, this
local workflow can run offline. It has no per-question API charges.

## Troubleshooting

**`ollama` is not recognized:** Reopen PowerShell after installing Ollama.
Confirm that the Windows app installed successfully.

**Cannot connect to Ollama:** Open Ollama from the Start menu and rerun
`--check`. If you are running Ollama without its desktop app, `ollama serve`
starts the server in a terminal. Use this only if no server is already running;
an “address already in use” message usually means one is running already.

**Model not found:** Run `ollama list`, then
`ollama pull qwen2.5:7b` if it is missing. The configured name must match the
downloaded model name.

**Responses are too slow:** Close memory-heavy apps. A CPU can take much
longer than a GPU. You can try a smaller, less capable model:

```powershell
ollama pull qwen2.5:3b
py -3.14 install_windows.py --model qwen2.5:3b
```

This is approximately a 2 GB download. It can improve speed, but it can make
more mistakes. Stop the program before changing its configuration, then start
it again. In `config.json`, you can raise `api_timeout_seconds` from `180` up
to `600` if the local model needs more time.

**Only an icon appears on the taskbar:** Set **Combine taskbar buttons and
hide labels** to **Never** as described above. Windows controls whether a
caption is visible; a taskbar button cannot override hidden labels. Check the
setting for the monitor on which you are reading the taskbar.

**A long answer is cut off:** Open
`%LOCALAPPDATA%\QuestionVolume\answer.txt` for the complete answer. Windows
controls taskbar button width, so even **Never** cannot guarantee that a long
fill-in answer fits across the taskbar.

**Nothing happens after a click:** Run
`.\.venv\Scripts\python.exe -m question_volume --check` and inspect
`%LOCALAPPDATA%\QuestionVolume\app.log`. Keep the complete question and options
visible. Tesseract reads text; it cannot reliably convey diagrams to this
text-only model workflow. OCR errors, missing options, solver errors, and
model abstentions clear the answer.

**Answers are wrong:** Local models can make mistakes even with clear OCR.
Verify important answers yourself. Larger models may help but need more
resources; a successful connection test is not an accuracy guarantee.
