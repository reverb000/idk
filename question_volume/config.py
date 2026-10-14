import json
import math
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    backend: str = "ollama"
    model: str = "qwen2.5:7b"
    capture_width: int = 1400
    capture_height: int = 1000
    step_percent: float = 1
    direction: str = "auto"
    cooldown_seconds: float = 0
    suppress_right_click: bool = True
    tesseract_cmd: str | None = None
    language: str = "eng"
    min_confidence: float = 40
    api_timeout_seconds: float = 180

    @classmethod
    def load(cls, path: Path):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object.")
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown configuration setting.")
        settings = cls(**data)
        for name in ("capture_width", "capture_height"):
            value = getattr(settings, name)
            if type(value) is not int or not 300 <= value <= 4000:
                raise ValueError(f"{name} must be an integer between 300 and 4000.")
        for name, low, high in (
            ("step_percent", 0.1, 10),
            ("cooldown_seconds", 0, 60),
            ("min_confidence", 0, 100),
            ("api_timeout_seconds", 5, 600),
        ):
            value = getattr(settings, name)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not low <= value <= high):
                raise ValueError(f"{name} must be between {low} and {high}.")
        if settings.direction not in ("auto", "up", "down"):
            raise ValueError("direction must be auto, up, or down.")
        if settings.backend not in ("ollama", "openai"):
            raise ValueError("backend must be ollama or openai.")
        if type(settings.suppress_right_click) is not bool:
            raise ValueError("suppress_right_click must be true or false.")
        for name in ("model", "language"):
            if not isinstance(getattr(settings, name), str) or not getattr(settings, name).strip():
                raise ValueError(f"{name} must be a nonempty string.")
        if settings.tesseract_cmd is not None and not isinstance(settings.tesseract_cmd, str):
            raise ValueError("tesseract_cmd must be a path or null.")
        return settings


def configure_solver(path: Path, backend: str, model: str):
    """Update local backend settings while preserving capture preferences."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object.")
    data.update(backend=backend, model=model)
    Settings.from_dict(data)  # Validate before replacing any user file.
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
