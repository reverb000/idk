from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    label: str
    text: str


@dataclass(frozen=True)
class Question:
    prompt: str
    choices: tuple[Choice, ...]
    kind: str = "choice"


@dataclass(frozen=True)
class Answer:
    kind: str
    labels: tuple[str, ...] = ()
    text: str | None = None
