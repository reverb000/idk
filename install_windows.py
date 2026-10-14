"""Install the Windows app without running a PowerShell script.

Run ``py -3.14 install_windows.py`` from an extracted copy of the program.
Python 3.12 is also supported via ``py -3.12 install_windows.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
SUPPORTED_PYTHONS = {(3, 12), (3, 14)}
VERSION_PROBE = "import sys; print('%d.%d' % sys.version_info[:2])"


def check_runtime() -> None:
    if sys.platform != "win32":
        raise RuntimeError("This installer must run on Windows 10 or Windows 11.")
    if sys.version_info[:2] not in SUPPORTED_PYTHONS:
        raise RuntimeError(
            "Run this installer with Python 3.14 (recommended) or Python 3.12: "
            "py -3.14 install_windows.py"
        )


def run_step(arguments: list[str], root: Path) -> None:
    """Execute an argument list directly, without a command shell."""
    subprocess.run(arguments, check=True, cwd=root)


def existing_python_version(python: Path, root: Path) -> tuple[int, int]:
    result = subprocess.run(
        [str(python), "-c", VERSION_PROBE],
        check=True,
        cwd=root,
        capture_output=True,
        text=True,
    )
    match = re.fullmatch(r"(\d+)\.(\d+)", result.stdout.strip())
    if match is None:
        raise RuntimeError(
            "Could not identify the existing .venv. Extract the program into a fresh folder."
        )
    return int(match[1]), int(match[2])


def prepare_environment(root: Path) -> Path:
    environment = root / ".venv"
    python = environment / "Scripts" / "python.exe"
    if environment.exists():
        if not python.is_file():
            raise RuntimeError(
                "The existing .venv is incomplete. Extract the program into a fresh folder; "
                "this installer will leave the existing folder untouched."
            )
        version = existing_python_version(python, root)
        selected = sys.version_info[:2]
        if version != selected:
            raise RuntimeError(
                f"Existing .venv uses Python {version[0]}.{version[1]}, but this installer "
                f"uses {selected[0]}.{selected[1]}. Run with py -{version[0]}.{version[1]} "
                "or extract the program into a fresh folder."
            )
        print("Keeping the existing Python environment.", flush=True)
    else:
        print("Creating the Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    if not python.is_file():
        raise RuntimeError("Python environment creation did not produce Scripts\\python.exe.")
    return python


def create_shortcuts(root: Path) -> None:
    """Run inside the app's environment, where comtypes has been installed."""
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        raise RuntimeError("The Python environment is missing Scripts\\pythonw.exe.")
    from comtypes.client import CreateObject

    shell = CreateObject("WScript.Shell", dynamic=True)
    for name in ("start", "stop"):
        path = root / f"{name}.lnk"
        if path.exists():
            print(f"Keeping existing {name}.lnk.", flush=True)
            continue
        shortcut = shell.CreateShortcut(str(path))
        shortcut.TargetPath = str(pythonw)
        shortcut.Arguments = subprocess.list2cmdline([str(root / f"{name}.pyw")])
        shortcut.WorkingDirectory = str(root)
        shortcut.Save()


def install(root: Path, backend: str, model: str | None) -> None:
    for required in ("requirements.txt", "start.pyw", "stop.pyw"):
        if not (root / required).is_file():
            raise RuntimeError(
                f"Missing {required}. Extract the whole program before running this installer."
            )
    python = prepare_environment(root)
    print("Installing dependencies...", flush=True)
    run_step([str(python), "-m", "pip", "install", "-r", "requirements.txt"], root)
    selected_model = model or ("qwen2.5:7b" if backend == "ollama" else "gpt-4.1-mini")
    print(f"Configuring the {backend} solver...", flush=True)
    run_step(
        [str(python), "-m", "question_volume", "--configure-backend", backend,
         "--model", selected_model],
        root,
    )
    if backend == "openai":
        run_step([str(python), "-m", "question_volume", "--set-api-key"], root)
    print("Checking OCR, taskbar, and solver prerequisites...", flush=True)
    run_step([str(python), "-m", "question_volume", "--check"], root)
    print("Creating start and stop shortcuts...", flush=True)
    # Use the venv interpreter so COM dependencies need not be installed globally.
    run_step([str(python), str(root / "install_windows.py"), "--create-shortcuts"], root)
    print("Setup complete. Double-click start.lnk to run and stop.lnk to stop.", flush=True)
    print("Keep Ollama running when using the local model.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--model", help="Defaults to qwen2.5:7b for Ollama.")
    parser.add_argument("--create-shortcuts", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        check_runtime()
        if arguments.create_shortcuts:
            create_shortcuts(ROOT)
        else:
            install(ROOT, arguments.backend, arguments.model)
    except subprocess.CalledProcessError as error:
        print(
            f"Setup stopped because a required command failed (exit {error.returncode}). "
            "Read the command's message above, correct that issue, and run the installer again.",
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, ImportError) as error:
        print(f"Setup stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
