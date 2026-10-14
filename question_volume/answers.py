"""Shared answer schema, validation, and taskbar formatting."""

from .models import Answer, Question


def answer_schema(question):
    labels = [choice.label for choice in question.choices]
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["single_choice", "multi_select", "fill_blank", "abstain"]},
            "labels": {"type": "array", "items": {"type": "string", "enum": labels or ["A"]}},
            "text": {"type": ["string", "null"]},
        },
        "required": ["kind", "labels", "text"],
        "additionalProperties": False,
    }


def parse_answer(data, question: Question) -> Answer | None:
    if not isinstance(data, dict) or set(data) != {"kind", "labels", "text"}:
        raise ValueError("Invalid answer shape.")
    kind, labels, text = data["kind"], data["labels"], data["text"]
    if not isinstance(kind, str) or not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("Invalid answer fields.")
    if kind == "abstain":
        if labels or text is not None:
            raise ValueError("Abstention cannot contain an answer.")
        return None
    if kind in ("single_choice", "multi_select"):
        allowed = {choice.label for choice in question.choices}
        if (question.kind != "choice" or not labels or len(set(labels)) != len(labels)
                or any(label not in allowed for label in labels) or text is not None
                or (kind == "single_choice" and len(labels) != 1)):
            raise ValueError("Invalid selected answer choices.")
        return Answer(kind, tuple(labels))
    if kind == "fill_blank":
        if (question.kind != "fill_blank" or question.choices or labels
                or not isinstance(text, str) or not text.strip() or len(text) > 2000
                or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in text)):
            raise ValueError("Invalid fill-in answer.")
        return Answer(kind, text=text.strip())
    raise ValueError("Unknown answer kind.")


def taskbar_text(answer: Answer, question: Question) -> str:
    checked = parse_answer({"kind": answer.kind, "labels": list(answer.labels), "text": answer.text}, question)
    if checked is None:
        raise ValueError("No answer to display.")
    if checked.kind == "fill_blank":
        return checked.text
    selected = set(checked.labels)
    # Number by the visible choice order, so A/D/F become 1,4,6.
    return ",".join(str(index) for index, choice in enumerate(question.choices, 1) if choice.label in selected)
