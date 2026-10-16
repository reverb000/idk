"""Start this folder's installed Windows app without using file associations.

Only standard-library imports belong here: the interpreter chosen to open a
``.pyw`` file may not have any of the application's dependencies installed.
"""

from pathlib import Path
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parent


def _report(message, stream=None):
    """Report a local startup error even when pythonw has no stderr."""
    line = "Question Taskbar startup failed: " + message + "\n"
    if stream is not None:
        try:
            stream.write(line.encode("utf-8"))
            stream.flush()
        except OSError:
            pass
    if sys.stderr is not None:
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


def launch(arguments=(), *, root=None):
    """Spawn the sibling virtual environment and return without waiting.

    The child imports the app through ``-m`` rather than another launcher, so a
    global Python file association cannot cause recursive relaunching.
    """
    root = Path(root).resolve() if root is not None else ROOT
    state_base = os.environ.get("LOCALAPPDATA")
    if not state_base:
        _report("LOCALAPPDATA is unavailable; run this launcher on Windows.")
        return 1
    try:
        state = Path(state_base) / "QuestionVolume"
        state.mkdir(parents=True, exist_ok=True)
        stream = (state / "startup.log").open("ab")
    except OSError as error:
        _report("Could not open startup.log (%s)." % type(error).__name__)
        return 1
    with stream:
        pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        if not pythonw.is_file():
            _report("This folder has no installed Python environment. "
                    "Extract the whole ZIP and run setup.cmd first.", stream)
            return 1
        try:
            stream.write(b"Launching Question Taskbar with this folder's Python environment.\n")
            stream.flush()
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
            subprocess.Popen(
                [str(pythonw), "-m", "question_volume", *arguments],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
        except OSError as error:
            _report("Could not start the installed interpreter (%s). "
                    "Run setup.cmd again." % type(error).__name__, stream)
            return 1
    return 0
