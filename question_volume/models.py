from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    label: str
    text: str


@dataclass(frozen=True)
class Question:
    prompt: str
    choices: tuple[Choice, ...]
