"""Keep taskbar results matched to the latest click, with a complete local text copy."""

import logging
from pathlib import Path
import threading


LOG = logging.getLogger("question_volume")


class AnswerOutput:
    def __init__(self, display, stop: threading.Event, answer_path: Path | None = None):
        self.display = display
        self.stop = stop
        self.answer_path = answer_path
        self.generation = 0
        self.lock = threading.Lock()
        self.file_lock = threading.Lock()

    def begin(self):
        # Only memory operations and an asynchronous UI message occur on the mouse hook.
        with self.lock:
            self.generation += 1
            token = self.generation
            self.display.clear("…")
            return token

    def current(self, token):
        with self.lock:
            return token == self.generation and not self.stop.is_set()

    def clear_saved(self, token):
        if self.answer_path is None:
            return
        try:
            with self.file_lock:
                if self.current(token):
                    self.answer_path.unlink(missing_ok=True)
        except OSError:
            LOG.warning("The previous local answer text could not be cleared.")

    def fail(self, token, status="?", clear_file=True):
        with self.lock:
            if token != self.generation or self.stop.is_set():
                return
            self.display.clear(status)
        if clear_file:
            self.clear_saved(token)

    def publish(self, token, text):
        with self.lock:
            if token != self.generation or self.stop.is_set():
                return False
            self.display.show(text)
        if self.answer_path is not None:
            try:
                with self.file_lock:
                    if not self.current(token):
                        return False
                    self.answer_path.write_text(text, encoding="utf-8")
                    # A click during disk I/O must not leave its predecessor's text behind.
                    if not self.current(token):
                        self.answer_path.unlink(missing_ok=True)
            except OSError:
                LOG.warning("The full answer could not be saved to the local text file.")
        return True
