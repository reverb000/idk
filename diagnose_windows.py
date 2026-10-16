"""Check a Windows installation without capturing the screen or asking the model."""

import importlib
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
REQUIRED_MODULES = (
    "mss",
    "PIL.Image",
    "pytesseract",
    "pynput.mouse",
    "comtypes.client",
    "keyring.backends.Windows",
)


def module_name(error):
    """Print only a module identifier, never an exception's arbitrary message."""
    name = error.name
    if isinstance(name, str) and len(name) <= 200 and re.fullmatch(
        r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", name
    ):
        return name
    return "unknown"


def repair_command(root):
    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"

    python = root / ".venv" / "Scripts" / "python.exe"
    return (f"& {quote(python)} -m pip install --force-reinstall "
            f"-r {quote(root / 'requirements.txt')}")


def report_failure(label, error):
    if isinstance(error, ModuleNotFoundError):
        print(f"FAIL {label}: missing module '{module_name(error)}'.")
    else:
        print(f"FAIL {label}: {type(error).__name__}.")


def show_repair(root):
    print("\nTo reinstall this folder's Python packages, open PowerShell and run:")
    print(repair_command(root))
    print("Then double-click diagnose.cmd again.")


def main():
    print("Question Taskbar installation check")
    print(f"Python: {sys.executable}")
    print(f"Version: {sys.version.split()[0]}")
    print(f"Program folder: {ROOT}")
    if sys.platform != "win32":
        print("FAIL: Run diagnose.cmd on the Windows 10/11 computer using the program.")
        return 1

    # Set this before importing pynput, which can otherwise choose its own DPI mode.
    try:
        runtime = importlib.import_module("question_volume.runtime")
        runtime.enable_dpi_awareness()
    except Exception as error:
        report_failure("Windows initialization", error)
        if isinstance(error, ModuleNotFoundError):
            print("Extract the whole ZIP to a folder, then run setup.cmd there.")
        return 1

    failed = False
    missing = False
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
            print(f"OK import {name}")
        except Exception as error:
            failed = True
            missing |= isinstance(error, ModuleNotFoundError)
            report_failure(f"import {name}", error)
    if failed:
        if missing:
            show_repair(ROOT)
        print("Installation check failed; the background program cannot start yet.")
        return 1

    print("\nChecking OCR, configured model availability, and Windows taskbar APIs...")
    try:
        app = importlib.import_module("question_volume.app")
        result = app.main(["--check"])
    except Exception as error:
        report_failure("program prerequisites", error)
        if isinstance(error, ModuleNotFoundError):
            show_repair(ROOT)
        return 1
    if result != 0:
        print("Installation check failed. Read the prerequisite error above.")
        return 1
    print("\nInstallation check passed. You can now start the program.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
