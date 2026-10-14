import os
import shutil
import sys
import threading
import time
from pathlib import Path


def state_directory():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is unavailable; run on Windows.")
    path = Path(base) / "QuestionVolume"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_tesseract(configured=None):
    candidates = [configured, shutil.which("tesseract")]
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(name):
            base = Path(os.environ[name])
            candidates.extend([str(base / "Tesseract-OCR" / "tesseract.exe"),
                               str(base / "Programs" / "Tesseract-OCR" / "tesseract.exe")])
    if configured:
        candidates = [configured]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError("Install Tesseract OCR or set tesseract_cmd in config.json.")


def enable_dpi_awareness():
    import ctypes

    if sys.platform != "win32":
        raise RuntimeError("The desktop listener requires Windows 10/11.")
    # Must happen before importing the mouse listener or opening capture handles.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


class InstanceLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        import msvcrt

        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise RuntimeError("The program is already running.") from None
        return self

    def __exit__(self, *args):
        self.file.close()


class ClickGate:
    """Serialize admission of capture triggers and optionally debounce clicks."""

    def __init__(self, cooldown):
        self.cooldown = cooldown
        self.last_click = float("-inf")
        self.busy = False
        self.lock = threading.Lock()

    def claim(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.busy or now - self.last_click < self.cooldown:
                return False
            self.busy = True
            self.last_click = now
            return True

    def release(self):
        with self.lock:
            self.busy = False
