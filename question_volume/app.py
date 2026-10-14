import argparse
import getpass
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import sys
import threading

from .capture import screenshot_near_cursor
from .config import Settings, configure_solver
from .credentials import api_key, save_api_key
from .runtime import ClickGate, InstanceLock, enable_dpi_awareness, find_tesseract, state_directory
from .volume import WindowsVolume

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("question_volume")


def configure_logging(path):
    handler = RotatingFileHandler(path / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)


def initialize(settings):
    from .ocr import OCRReader
    from .solver import OpenAIAnswerSolver
    import pytesseract

    tesseract = find_tesseract(settings.tesseract_cmd)
    pytesseract.pytesseract.tesseract_cmd = tesseract
    pytesseract.get_tesseract_version()
    if settings.language not in pytesseract.get_languages(config=""):
        raise RuntimeError("The configured OCR language is not installed.")
    reader = OCRReader(tesseract, settings.language, settings.min_confidence)
    if settings.backend == "ollama":
        from .local_solver import OllamaAnswerSolver

        solver = OllamaAnswerSolver(settings.model, timeout=settings.api_timeout_seconds)
        solver.check_model()
    else:
        key = api_key()
        if not key:
            raise RuntimeError("No OpenAI API key. Run python -m question_volume --set-api-key.")
        solver = OpenAIAnswerSolver(key, model=settings.model, timeout=settings.api_timeout_seconds)
    return reader, solver, WindowsVolume(settings.step_percent, settings.direction)


def process_capture(image, cursor, reader, solver, volume, stop):
    question = reader.read(image, cursor)
    if question is None:
        LOG.info("No complete multiple-choice question found near the cursor.")
        return
    if stop.is_set():
        return
    answer = solver.solve(question)
    if answer is None:
        LOG.info("The model abstained; volume unchanged.")
        return
    if answer not in {choice.label for choice in question.choices}:
        raise ValueError("Solver returned a choice outside the question.")
    if stop.is_set():
        return
    change = volume.signal(answer)
    if change is None:
        LOG.info("Insufficient volume headroom; volume unchanged.")
    else:
        LOG.info("Answer %s; volume change %+.2f percentage points.", answer, change)


def listen(settings, reader, solver, volume, state):
    from pynput import mouse

    gate = ClickGate(settings.cooldown_seconds)
    clicks = queue.Queue(maxsize=5)
    captures = queue.Queue(maxsize=5)
    stop = threading.Event()
    stop_file = state / "stop.request"
    stop_file.unlink(missing_ok=True)

    def capture_worker():
        while not stop.is_set():
            try:
                cursor = clicks.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                item = screenshot_near_cursor(cursor, settings.capture_width, settings.capture_height)
                captures.put_nowait(item)
            except queue.Full:
                LOG.warning("Too many pending questions; this click was skipped.")
            except Exception as error:
                LOG.error("Screen capture failed (%s).", type(error).__name__)
            finally:
                clicks.task_done()

    def worker():
        while not stop.is_set():
            try:
                item = captures.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                process_capture(*item, reader, solver, volume, stop)
            except Exception as error:
                # Screen contents, credentials, API payloads, and tracebacks are never logged.
                from .solver import SolverError
                if isinstance(error, SolverError):
                    LOG.error("%s Volume unchanged.", error)
                else:
                    LOG.error("Question processing failed (%s); signaling did not complete.",
                              type(error).__name__)
            finally:
                captures.task_done()

    def on_click(x, y, button, pressed):
        if stop.is_set() or button != mouse.Button.right or not pressed or not gate.claim():
            return
        try:
            # Native hooks must return immediately: never do capture/OCR/HTTP here.
            clicks.put_nowait((x, y))
        except queue.Full:
            LOG.warning("Too many pending clicks; this click was skipped.")
        finally:
            gate.release()

    def event_filter(message, data):
        # Suppress only right-button events; left clicks, scrolling, and keyboard work normally.
        if settings.suppress_right_click and message in (0x0204, 0x0205, 0x0206):
            if message == 0x0204:
                on_click(data.pt.x, data.pt.y, mouse.Button.right, True)
            listener.suppress_event()
        return True

    thread = threading.Thread(target=worker, name="question-worker", daemon=True)
    capture_thread = threading.Thread(target=capture_worker, name="screen-capture", daemon=True)
    thread.start()
    capture_thread.start()
    listener = mouse.Listener(on_click=on_click, win32_event_filter=event_filter)
    listener.start()
    LOG.info("Listening for right-clicks; no application windows are opened.")
    try:
        while not stop.wait(0.2):
            if stop_file.exists():
                break
            if not listener.is_alive():
                raise RuntimeError("The mouse listener stopped unexpectedly.")
    finally:
        stop.set()
        listener.stop()
        listener.join(timeout=2)
        # A running HTTP request is bounded by the configured timeout; the daemon can exit.
        thread.join(timeout=0.5)
        capture_thread.join(timeout=0.5)
        stop_file.unlink(missing_ok=True)
        LOG.info("Stopped.")


def check(settings):
    import comtypes
    from pycaw.pycaw import AudioUtilities

    initialize(settings)
    comtypes.CoInitialize()
    device = endpoint = None
    try:
        device = AudioUtilities.GetSpeakers()
        endpoint = device.EndpointVolume
        endpoint.GetMasterVolumeLevelScalar()
    finally:
        endpoint = None
        device = None
        comtypes.CoUninitialize()
    backend_check = "Ollama server and downloaded model" if settings.backend == "ollama" else "API-key presence"
    print(f"Local checks passed: OCR engine, language, {backend_check}, and default audio output.")
    print("Run --check-solver to test one answer without changing volume, then try a real right-click.")


def check_solver(settings):
    from .models import Choice, Question

    _, solver, _ = initialize(settings)
    question = Question("What is 2 plus 2?", (Choice("A", "3"), Choice("B", "4"), Choice("C", "5"), Choice("D", "6")))
    answer = solver.solve(question)
    if answer != "B":
        raise RuntimeError("The solver did not answer the sample correctly. Try a stronger model or check configuration.")
    print("Solver check passed: the sample question returned B. Volume was not changed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Right-click OCR and local AI volume signaling (Windows).")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--set-api-key", action="store_true", help="Store a key securely in Windows Credential Manager.")
    parser.add_argument("--stop", action="store_true", help="Stop the background instance.")
    parser.add_argument("--check", action="store_true", help="Check local prerequisites without changing volume.")
    parser.add_argument("--check-solver", action="store_true", help="Answer a sample question without changing volume.")
    parser.add_argument("--configure-backend", choices=("ollama", "openai"), help="Set the backend in local configuration.")
    parser.add_argument("--model", help="Model name to save with --configure-backend.")
    args = parser.parse_args(argv)
    try:
        if args.configure_backend:
            model = args.model or ("qwen2.5:7b" if args.configure_backend == "ollama" else "gpt-4.1-mini")
            configure_solver(args.config, args.configure_backend, model)
            print("Answer solver configuration saved.")
            return 0
        if args.model:
            raise ValueError("--model must be used with --configure-backend.")
        if sys.platform != "win32":
            raise RuntimeError("This desktop program runs on Windows 10/11.")
        if args.set_api_key:
            save_api_key(getpass.getpass("OpenAI API key (input hidden): "))
            print("API key saved in Windows Credential Manager.")
            return 0
        state = state_directory()
        configure_logging(state)
        if args.stop:
            (state / "stop.request").touch()
            return 0
        settings = Settings.load(args.config)
        enable_dpi_awareness()
        if args.check:
            check(settings)
            return 0
        if args.check_solver:
            check_solver(settings)
            return 0
        with InstanceLock(state / "instance.lock"):
            reader, solver, volume = initialize(settings)
            listen(settings, reader, solver, volume, state)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # Only known local validation messages are shown; external errors can include sensitive data.
        if isinstance(error, (ValueError, RuntimeError)):
            message = str(error)
        else:
            message = f"Startup failed ({type(error).__name__}); check installation and configuration."
        LOG.error("%s", message)
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1
