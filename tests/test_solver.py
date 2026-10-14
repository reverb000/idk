"""Contract tests for text-only answer requests and fail-closed responses."""

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from question_volume.models import Choice, Question
from question_volume.solver import OpenAIAnswerSolver, SolverError


def response_body(answer="B"):
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps({"answer": answer})}],
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

    def solve_body(self, body):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        with patch("question_volume.solver.urlopen", return_value=io.BytesIO(raw)):
            return self.solver.solve(self.question)

    def test_request_sends_text_and_strict_actual_choices_only(self):
        with patch(
            "question_volume.solver.urlopen",
            return_value=io.BytesIO(json.dumps(response_body()).encode()),
        ) as mocked:
            self.assertEqual(self.solver.solve(self.question), "B")
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(mocked.call_args.kwargs, {"timeout": 30.0})
        payload = json.loads(request.data)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["model"], "gpt-4.1-mini")
        self.assertGreaterEqual(payload["max_output_tokens"], 64)
        self.assertIn("untrusted OCR", payload["instructions"])
        self.assertIn("missing diagram", payload["instructions"])
        self.assertEqual(
            json.loads(payload["input"]),
            {"question": "What is 2 + 2?", "choices": [{"label": "A", "text": "3"}, {"label": "B", "text": "4"}]},
        )
        format_spec = payload["text"]["format"]
        self.assertEqual(format_spec["type"], "json_schema")
        self.assertTrue(format_spec["strict"])
        self.assertEqual(format_spec["schema"]["properties"]["answer"]["enum"], ["A", "B", None])
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
            {"type": "output_text", "text": '{"answer":'},
            {"type": "output_text", "text": '"B"}'},
        ]
        self.assertEqual(self.solve_body(body), "B")

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
