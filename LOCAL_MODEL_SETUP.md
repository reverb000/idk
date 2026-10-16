# Run Question Taskbar with a local AI model on Windows

For your computer with **16 GB or more RAM**, start with **Ollama** and
**Qwen 2.5 7B** (`qwen2.5:7b`). You do not need an OpenAI API key or a paid
ChatGPT subscription. The model runs on your computer. A compatible GPU can
make responses faster; it can also run on the CPU, more slowly.

Use the updated Question Taskbar edition. It shows answers in an empty part
of the Windows 11 taskbar instead of changing the volume. The earlier
OpenAI-only edition will not use Ollama just because you install it.

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
3. Download the [updated Windows ZIP](https://github.com/reverb000/idk/archive/refs/heads/codex/windows-question-taskbar.zip)
   and extract it into a folder you can keep, such as
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

## 3. Understand where the answer appears

No taskbar label or grouping settings need to be changed. Question Taskbar
adds no taskbar app button or new pinned icon. It uses a click-through text overlay in a measured
empty horizontal gap, leaving space around Start, Search, weather/widgets,
pinned and running applications, and the system tray and clock. It does not
move or resize the taskbar or its controls, and mouse clicks pass through the
overlay.

The program checks the layout about every 200 milliseconds and moves or
hides the overlay when necessary. It needs a readable gap at least 56
device-independent pixels wide after margins. If no suitable gap is available,
the layout cannot be measured reliably, or a full-screen application is
active, no answer is drawn. Hiding is intentional
when the program cannot find a suitable location. Windows layout changes
can occur between measurements; the overlay does not reserve taskbar space.

Leave **Automatically hide the taskbar** off under **Settings →
Personalization → Taskbar → Taskbar behaviors** if you want the answer overlay.
The overlay stays hidden whenever this option is enabled, even while the
taskbar is visible on hover. This conservative limit avoids placing text
where Windows may be animating or clipping the taskbar. The program does not
change the setting automatically.

Long answers scroll inside the available gap. The complete answer is also
saved as UTF-8 text at `%LOCALAPPDATA%\QuestionVolume\answer.txt`, even when
the overlay is hidden. You can paste this path into File Explorer's address
bar to read it. This file is local and may contain the most recent answer;
do not share it if that text is private.

## 4. Start and use it

1. Keep Ollama running. Its Windows desktop app serves models in the background.
2. Double-click **start.lnk**. Question Taskbar runs without opening a
   foreground window or taking focus.
3. Show the complete question. For multiple choice or multiple selections,
   include all lettered answer options. For a fill-in question, include the
   complete prompt and blank. Move the cursor near the question and
   **right-click once**.
4. Wait for OCR and the model to finish. The first response can take longer
   because the model needs to load. Read the answer in the taskbar gap:
   **A → `1`** through **Z → `26`**, any multiselect such as
   **B, E, Z → `2,5,26`** or **A, D, F → `1,4,6`**, or the returned fill-in
   text. Multiselect answers use choice order, with commas between numbers;
   the examples do not limit which letters can be selected.
   A new accepted click clears the previous answer and shows a processing
   indicator if there is room. Only the newest question's result appears;
   errors clear the answer so an old result is not mistaken for a new one.
5. Double-click **stop.lnk** when finished. While Question Taskbar runs, it
   consumes question-area right-clicks; stopping restores normal right-click
   menus everywhere. Taskbar right-clicks always pass through so pinned-app
   menus and system controls keep working.

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

**An answer is saved, but no taskbar text appears:** Leave full-screen mode
and ensure **Automatically hide the taskbar** is off. Hovering to reveal an
auto-hidden taskbar does not enable the overlay. The program also hides it if
the taskbar is too crowded or Windows does not provide reliable measurements
of its controls.
It will not place the answer beside Search, weather, apps, or the tray without
a measured gap and buffer. No taskbar label or grouping settings need to be
changed. Open `%LOCALAPPDATA%\QuestionVolume\answer.txt` for the answer.

**A long answer does not fit at once:** Let the text scroll within its
available gap, or open `%LOCALAPPDATA%\QuestionVolume\answer.txt` for the
complete answer.

**Nothing happens after a click:** Run
`.\.venv\Scripts\python.exe -m question_volume --check` and inspect
`%LOCALAPPDATA%\QuestionVolume\app.log`. Keep the complete question and options
visible. Tesseract reads text; it cannot reliably convey diagrams to this
text-only model workflow. OCR errors, missing options, solver errors, and
model abstentions clear the answer.

**Answers are wrong:** Local models can make mistakes even with clear OCR.
Verify important answers yourself. Larger models may help but need more
resources; a successful connection test is not an accuracy guarantee.

The automated checks use Linux, mocked Windows APIs, synthetic taskbar
geometry, and real OCR when available. Native UI Automation, empty-space
placement, scrolling, click-through and focus behavior still need checking on
a real Windows desktop; the automated checks do not confirm that behavior.
