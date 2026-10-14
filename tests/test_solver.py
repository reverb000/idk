"""Contract tests for text-only answer requests and fail-closed responses."""

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from question_volume.answers import taskbar_text
from question_volume.models import Answer, Choice, Question
from question_volume.solver import OpenAIAnswerSolver, SolverError


def response_body(answer="B"):
    if not isinstance(answer, dict):
        answer = {"kind": "abstain", "labels": [], "text": None} if answer is None else {
            "kind": "single_choice", "labels": [answer], "text": None,
        }
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(answer)}],
            }
        ],
    }


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.question = Question(
            prompt="What is 2 + 2?",
            choices=(Choice(label="A", text="3"), Choice(label="B", text="4")),
        )
        self.solver = OpenAIAnswerSolver("test-key-that-must-stay-private")

    def solve_body(self, body, question=None):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        with patch("question_volume.solver.urlopen", return_value=io.BytesIO(raw)):
            return self.solver.solve(question or self.question)

    def test_request_sends_text_and_strict_actual_choices_only(self):
        with patch(
            "question_volume.solver.urlopen",
            return_value=io.BytesIO(json.dumps(response_body()).encode()),
        ) as mocked:
            self.assertEqual(self.solver.solve(self.question), Answer("single_choice", ("B",)))
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(mocked.call_args.kwargs, {"timeout": 30.0})
        payload = json.loads(request.data)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "gpt-4.1-mini")
        self.assertGreaterEqual(payload["max_output_tokens"], 512)
        self.assertIn("untrusted OCR", payload["instructions"])
        self.assertIn("missing diagram", payload["instructions"])
        self.assertEqual(
            json.loads(payload["input"]),
            {"kind": "choice", "question": "What is 2 + 2?", "choices": [{"label": "A", "text": "3"}, {"label": "B", "text": "4"}]},
        )
        format_spec = payload["text"]["format"]
        self.assertEqual(format_spec["type"], "json_schema")
        self.assertTrue(format_spec["strict"])
        self.assertEqual(format_spec["schema"]["properties"]["labels"]["items"]["enum"], ["A", "B"])
        self.assertEqual(format_spec["schema"]["required"], ["kind", "labels", "text"])
        self.assertFalse(format_spec["schema"]["additionalProperties"])
        self.assertNotIn("image", payload["input"])

    def test_abstention(self):
        self.assertIsNone(self.solve_body(response_body(None)))

    def test_refusal_abstains(self):
        body = response_body()
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "Cannot answer."}]
        self.assertIsNone(self.solve_body(body))

    def test_unrecognized_and_nonexact_labels_are_rejected(self):
        for answer in ("C", "b", " B ", "The answer is B", 2, ["B"]):
            with self.subTest(answer=answer), self.assertRaises(SolverError):
                self.solve_body(response_body(answer))

    def test_json_shape_and_duplicate_keys_are_rejected(self):
        for text in (
            '```json\n{"answer":"B"}\n```',
            '{"answer":"B", "explanation":"because"}',
            '{"answer":"A", "answer":"B"}',
            '{"kind":"abstain","kind":"single_choice","labels":["B"],"text":null}',
            '{"kind":"single_choice","labels":["B"],"text":null,"explanation":"because"}',
            '{}',
            '["B"]',
            'not JSON',
        ):
            with self.subTest(text=text), self.assertRaises(SolverError):
                body = response_body()
                body["output"][0]["content"][0]["text"] = text
                self.solve_body(body)

    def test_incomplete_response_never_selects_a_choice(self):
        body = response_body()
        body["status"] = "incomplete"
        with self.assertRaises(SolverError):
            self.solve_body(body)
        body = response_body()
        body["output"][0]["status"] = "incomplete"
        with self.assertRaises(SolverError):
            self.solve_body(body)

    def test_unsupported_output_shapes_are_rejected(self):
        bad_bodies = [None, {}, {"status": "completed", "output": []}]
        body = response_body()
        body["output"].append(body["output"][0])
        bad_bodies.append(body)
        body = response_body()
        body["output"][0]["content"] = [{"type": "unknown", "text": '{"answer":"B"}'}]
        bad_bodies.append(body)
        for body in bad_bodies:
            with self.subTest(body=body), self.assertRaises(SolverError):
                self.solve_body(body)

    def test_split_text_blocks_and_reasoning_item(self):
        body = response_body()
        body["output"].insert(0, {"type": "reasoning", "summary": []})
        body["output"][1]["content"] = [
            {"type": "output_text", "text": '{"kind":"single_choice",'},
            {"type": "output_text", "text": '"labels":["B"],"text":null}'},
        ]
        self.assertEqual(self.solve_body(body), Answer("single_choice", ("B",)))

    def test_multi_select_preserves_valid_labels_and_formats_by_choice_order(self):
        question = Question("Select all correct statements.", tuple(Choice(label, label) for label in "ABCDEF"))
        for labels in (["A", "D", "F"], ["F", "A", "D"]):
            with self.subTest(labels=labels):
                answer = self.solve_body(response_body({"kind": "multi_select", "labels": labels, "text": None}), question)
                self.assertEqual(answer, Answer("multi_select", tuple(labels)))
                self.assertEqual(taskbar_text(answer, question), "1,4,6")

    def test_fill_blank_returns_short_exact_answer(self):
        question = Question("The chemical symbol for gold is ____.", (), "fill_blank")
        body = response_body({"kind": "fill_blank", "labels": [], "text": "Au"})
        self.assertEqual(self.solve_body(body, question), Answer("fill_blank", text="Au"))
        with patch("question_volume.solver.urlopen", return_value=io.BytesIO(json.dumps(body).encode())) as mocked:
            self.solver.solve(question)
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(json.loads(payload["input"]), {
            "kind": "fill_blank", "question": question.prompt, "choices": [],
        })
        self.assertEqual(payload["text"]["format"]["schema"]["properties"]["text"]["type"], ["string", "null"])
        self.assertIn("without explanations or prefixes", payload["instructions"])
        self.assertIsNone(self.solve_body(response_body(None), question))

    def test_invalid_structured_answers_are_rejected(self):
        invalid = [
            {"kind": "single_choice", "labels": [], "text": None},
            {"kind": "single_choice", "labels": ["A", "B"], "text": None},
            {"kind": "single_choice", "labels": ["B"], "text": "private answer"},
            {"kind": "multi_select", "labels": ["A", "A"], "text": None},
            {"kind": "multi_select", "labels": ["A", "C"], "text": None},
            {"kind": "abstain", "labels": ["A"], "text": None},
            {"kind": "abstain", "labels": [], "text": "private answer"},
            {"kind": "fill_blank", "labels": [], "text": "private answer"},
            {"kind": "unknown", "labels": [], "text": None},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(SolverError) as caught:
                self.solve_body(response_body(data))
            self.assertNotIn("private", str(caught.exception))

    def test_fill_blank_rejects_choices_blank_long_and_multiline_answers(self):
        question = Question("The answer is ____.", (), "fill_blank")
        invalid = [
            {"kind": "single_choice", "labels": ["A"], "text": None},
            {"kind": "fill_blank", "labels": ["A"], "text": "answer"},
        ]
        invalid.extend({"kind": "fill_blank", "labels": [], "text": text}
                       for text in (None, "", " ", "x" * 2001, "a\nb", "a\tb", "a\x00b"))
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(SolverError):
                self.solve_body(response_body(data), question)

    def test_http_errors_never_include_key_or_server_body(self):
        for status in (400, 401, 403, 429, 500):
            error = HTTPError(
                "https://api.openai.com/v1/responses", status,
                "test-key-that-must-stay-private", {}, io.BytesIO(b"private screen text"),
            )
            with self.subTest(status=status), patch("question_volume.solver.urlopen", side_effect=error) as mocked:
                with self.assertRaises(SolverError) as caught:
                    self.solver.solve(self.question)
                self.assertNotIn("private", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                mocked.assert_called_once()

    def test_network_errors_are_safe_and_not_retried(self):
        for error in (URLError("private connection details"), TimeoutError("private data"), OSError("private data")):
            with self.subTest(error=type(error).__name__), patch("question_volume.solver.urlopen", side_effect=error) as mocked:
                with self.assertRaises(SolverError) as caught:
                    self.solver.solve(self.question)
                self.assertNotIn("private", str(caught.exception))
                mocked.assert_called_once()

    def test_unreadable_and_oversized_responses_are_rejected(self):
        for raw in (b"not json", b"\xff", b" " * (256 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(SolverError):
                self.solve_body(raw)

    def test_incomplete_input_skips_the_api(self):
        cases = [
            Question(prompt="", choices=self.question.choices),
            Question(prompt="Question?", choices=()),
            Question(prompt="Question?", choices=(Choice(label="A", text="a"),)),
            Question(prompt="Question?", choices=(Choice(label="A", text="a"), Choice(label="A", text="b"))),
            Question(prompt="Question?", choices=(Choice(label="A", text="a"), Choice(label="B", text=" "))),
            Question(prompt="Question?", choices=(Choice(label="AA", text="a"), Choice(label="B", text="b"))),
            Question(prompt="Question?", choices=(Choice(label="a", text="a"), Choice(label="B", text="b"))),
            Question(prompt="Question?", choices=self.question.choices, kind="fill_blank"),
            Question(prompt=" ", choices=(), kind="fill_blank"),
            Question(prompt="Question?", choices=self.question.choices, kind="unknown"),
        ]
        with patch("question_volume.solver.urlopen") as mocked:
            for question in cases:
                self.assertIsNone(self.solver.solve(question))
            mocked.assert_not_called()

    def test_missing_key_and_invalid_configuration_fail_early(self):
        for key in ("", " ", None):
            with self.subTest(key=key), self.assertRaises(SolverError):
                OpenAIAnswerSolver(key)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(SolverError):
                OpenAIAnswerSolver("key", timeout=timeout)
        with self.assertRaises(SolverError):
            OpenAIAnswerSolver("key", model=" ")


if __name__ == "__main__":
    unittest.main()
