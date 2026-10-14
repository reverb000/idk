# Run Question Volume with a local AI model on Windows

For your computer with **16 GB or more RAM**, start with **Ollama** and
**Qwen 2.5 7B** (`qwen2.5:7b`). You do not need an OpenAI API key or a paid
ChatGPT subscription. The model runs on your computer. A compatible GPU can
make responses faster; it can also run on the CPU, more slowly.

Use the updated local-model edition of Question Volume. The earlier OpenAI-only
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
Leave the Ollama desktop app running; Question Volume talks to its background
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
3. Extract the updated Question Volume ZIP into a folder you can keep, such
   as `Documents\QuestionVolume`. Open that folder in File Explorer. Open
   PowerShell in this folder by typing `powershell` into its address bar and
   pressing Enter.
4. Double-click **setup.cmd** in the extracted folder. Alternatively, run:
   ```cmd
   py -3.14 install_windows.py
   ```
   This installs the Python packages, configures `qwen2.5:7b`, checks your OCR,
   audio, and Ollama prerequisites, and creates **start.lnk** and **stop.lnk**.
   It does not ask for an OpenAI API key.
   This uses Python rather than a PowerShell script, so `setup.ps1` does not
   need to be enabled. The installer window stays open to show errors.
   When moving from Python 3.12 to 3.14, use a fresh extracted folder so the
   existing virtual environment and your files remain intact.
5. Test a real model response:
   ```powershell
   .\.venv\Scripts\python.exe -m question_volume --check-solver
   ```
   This checks a known-answer question without capturing your screen or
   changing the volume. A pass confirms that the local solver responds;
   it does not guarantee the answers to other questions will be correct.

If PowerShell blocked `setup.ps1`, use `setup.cmd` or the Python command above.
The [README](README.md#install-on-your-windows-computer) also contains manual
commands. A fresh installation
uses the local model by default. If you have an existing `config.json`, ensure
it includes `"backend": "ollama"` and `"model": "qwen2.5:7b"`.
If that older configuration uses a 30-second timeout, set
`"api_timeout_seconds": 180` to allow a local model more time.

## 3. Start and use it

1. Keep Ollama running. Its Windows desktop app serves models in the background.
2. Double-click **start.lnk**. Question Volume opens no console or application
   window during normal use.
3. Show a full multiple-choice question and its lettered answer options. Move
   the cursor near the question and **right-click once**.
4. Wait for OCR and the model to finish. The first response can take longer
   because the model needs to load. The volume changes by **A = 1, B = 2,
   C = 3, D = 4 percentage points**. By default it alternates up and down
   after successful answers.
5. Double-click **stop.lnk** when finished. While Question Volume runs, it
   consumes right-clicks; stopping restores normal right-click menus.

The setup terminal and Ollama installation are visible. Routine Question
Volume operation does not open new windows; Ollama may have its own tray icon.
After installing and downloading the model, this local workflow can run
offline. It has no per-question API charges.

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

This is approximately a 2 GB download. It is a useful option for smaller
computers or better speed, but it can make more mistakes. Stop the program
before changing its configuration, then start it again. In `config.json`,
you can raise `api_timeout_seconds` from `180` up to `600` if the local model
needs more time.

**Nothing happens after a click:** Run
`.\.venv\Scripts\python.exe -m question_volume --check` and inspect
`%LOCALAPPDATA%\QuestionVolume\app.log`. Keep the complete question and options
visible. Tesseract reads text; it cannot reliably convey diagrams to this
text-only model workflow. OCR errors, missing options, solver errors, and
model abstentions leave the volume unchanged.

**Answers are wrong:** Local models can make mistakes even with clear OCR.
Verify important answers yourself. Larger models may help but need more
resources; a successful connection test is not an accuracy guarantee.
