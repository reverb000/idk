"""Verify local-only Ollama requests and fail-closed answer selection."""

import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from question_volume import local_solver
from question_volume.local_solver import OllamaAnswerSolver
from question_volume.models import Choice, Question
from question_volume.solver import SolverError


def response_body(answer="B"):
    return {
        "done": True,
        "model": "qwen2.5:7b",
        "message": {"role": "assistant", "content": json.dumps({"answer": answer})},
    }


def tags_body():
    return {"models": [{"name": "qwen2.5:7b", "model": "qwen2.5:7b"}]}


class LocalSolverTests(unittest.TestCase):
    def setUp(self):
        self.question = Question(
            prompt="What is 2 + 2?",
            choices=(Choice(label="A", text="3"), Choice(label="B", text="4")),
        )
        self.solver = OllamaAnswerSolver(model="qwen2.5:7b", timeout=180)

    def solve_body(self, body):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        with patch("question_volume.local_solver._open_local", return_value=io.BytesIO(raw)):
            return self.solver.solve(self.question)

    def check_body(self, body):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        with patch("question_volume.local_solver._open_local", return_value=io.BytesIO(raw)):
            return self.solver.check_model()

    def test_request_uses_loopback_text_and_actual_choice_schema_without_auth(self):
        with patch(
            "question_volume.local_solver._open_local",
            return_value=io.BytesIO(json.dumps(response_body()).encode()),
        ) as mocked:
            self.assertEqual(self.solver.solve(self.question), "B")
        mocked.assert_called_once()
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(mocked.call_args.kwargs, {"timeout": 180.0})
        self.assertNotIn("authorization", {key.lower() for key in request.headers})
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "qwen2.5:7b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["options"]["num_predict"], 128)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual([item["role"] for item in payload["messages"]], ["system", "user"])
        instructions = payload["messages"][0]["content"]
        self.assertIn("untrusted OCR", instructions)
        self.assertIn("missing diagram", instructions)
        self.assertEqual(
            json.loads(payload["messages"][1]["content"]),
            {
                "question": "What is 2 + 2?",
                "choices": [{"label": "A", "text": "3"}, {"label": "B", "text": "4"}],
            },
        )
        schema = payload["format"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["properties"]["answer"]["type"], ["string", "null"])
        self.assertEqual(schema["properties"]["answer"]["enum"], ["A", "B", None])
        self.assertEqual(schema["required"], ["answer"])
        self.assertFalse(schema["additionalProperties"])

    def test_abstention_returns_none(self):
        self.assertIsNone(self.solve_body(response_body(None)))

    def test_unrecognized_and_nonexact_labels_are_rejected(self):
        for answer in ("C", "b", " B ", "The answer is B", 2, ["B"], True):
            with self.subTest(answer=answer), self.assertRaises(SolverError):
                self.solve_body(response_body(answer))

    def test_json_shape_and_duplicate_answer_keys_are_rejected(self):
        for content in (
            '```json\n{"answer":"B"}\n```',
            '{"answer":"B", "explanation":"because"}',
            '{"answer":"A", "answer":"B"}',
            '{}',
            '["B"]',
            'not JSON',
        ):
            body = response_body()
            body["message"]["content"] = content
            with self.subTest(content=content), self.assertRaises(SolverError):
                self.solve_body(body)

    def test_only_completed_responses_are_accepted(self):
        for done in (False, None, "true", 1):
            body = response_body()
            body["done"] = done
            with self.subTest(done=done), self.assertRaises(SolverError):
                self.solve_body(body)
        body = response_body()
        del body["done"]
        with self.assertRaises(SolverError):
            self.solve_body(body)

    def test_incompatible_response_models_are_rejected(self):
        for model in ("another-model:7b", "qwen2.5:7b:cloud", None, 7):
            body = response_body()
            body["model"] = model
            with self.subTest(model=model), self.assertRaises(SolverError):
                self.solve_body(body)

    def test_response_errors_are_safe(self):
        body = response_body()
        body["error"] = "private screen text"
        with self.assertRaises(SolverError) as caught:
            self.solve_body(body)
        self.assertNotIn("private", str(caught.exception))

    def test_truncated_output_and_tool_calls_never_select_an_answer(self):
        body = response_body()
        body["done_reason"] = "length"
        with self.assertRaises(SolverError):
            self.solve_body(body)
        body = response_body()
        body["message"]["tool_calls"] = [{"function": {"name": "unexpected"}}]
        with self.assertRaises(SolverError):
            self.solve_body(body)

    def test_unsupported_message_and_response_shapes_are_rejected(self):
        bodies = [None, [], {}, {"done": True, "model": "qwen2.5:7b"}]
        for message in (None, [], {}, {"role": "user", "content": '{"answer":"B"}'},
                        {"role": "assistant", "content": None},
                        {"role": "assistant", "content": {"answer": "B"}}):
            body = response_body()
            body["message"] = message
            bodies.append(body)
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(SolverError):
                self.solve_body(body)

    def test_incomplete_input_skips_ollama(self):
        cases = [
            Question(prompt="", choices=self.question.choices),
            Question(prompt=" ", choices=self.question.choices),
            Question(prompt="Question?", choices=()),
            Question(prompt="Question?", choices=(Choice(label="A", text="a"),)),
            Question(prompt="Question?", choices=(Choice(label="A", text="a"), Choice(label="A", text="b"))),
            Question(prompt="Question?", choices=(Choice(label="A", text="a"), Choice(label="B", text=" "))),
        ]
        with patch("question_volume.local_solver._open_local") as mocked:
            for question in cases:
                self.assertIsNone(self.solver.solve(question))
            mocked.assert_not_called()

    def test_http_errors_do_not_expose_server_body_and_are_not_retried(self):
        for status in (400, 404, 429, 500):
            error = HTTPError(
                "http://127.0.0.1:11434/api/chat", status,
                "private connection details", {}, io.BytesIO(b"private screen text"),
            )
            with self.subTest(status=status), patch(
                "question_volume.local_solver._open_local", side_effect=error,
            ) as mocked:
                with self.assertRaises(SolverError) as caught:
                    self.solver.solve(self.question)
                self.assertNotIn("private", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                mocked.assert_called_once()

    def test_connection_errors_are_safe_and_not_retried(self):
        errors = (URLError("private connection details"), TimeoutError("private data"), OSError("private data"))
        for error in errors:
            with self.subTest(error=type(error).__name__), patch(
                "question_volume.local_solver._open_local", side_effect=error,
            ) as mocked:
                with self.assertRaises(SolverError) as caught:
                    self.solver.solve(self.question)
                self.assertNotIn("private", str(caught.exception))
                mocked.assert_called_once()

    def test_unreadable_and_oversized_responses_are_rejected(self):
        for raw in (b"not json", b"\xff", b" " * (256 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(SolverError):
                self.solve_body(raw)

    def test_duplicate_outer_keys_are_rejected(self):
        raw = b'{"done":false,"done":true,"model":"qwen2.5:7b","message":{"role":"assistant","content":"{\\"answer\\":\\"B\\"}"}}'
        with self.assertRaises(SolverError):
            self.solve_body(raw)

    def test_model_check_requests_tags_locally_without_auth(self):
        with patch(
            "question_volume.local_solver._open_local",
            return_value=io.BytesIO(json.dumps(tags_body()).encode()),
        ) as mocked:
            self.assertIsNone(self.solver.check_model())
        mocked.assert_called_once()
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertNotIn("authorization", {key.lower() for key in request.headers})

    def test_missing_model_reports_exact_pull_command(self):
        with self.assertRaises(SolverError) as caught:
            self.check_body({"models": [{"name": "some-other:7b", "model": "some-other:7b"}]})
        self.assertIn("ollama pull qwen2.5:7b", str(caught.exception))

    def test_selected_model_remote_metadata_is_rejected(self):
        for key, value in (("remote_model", "qwen2.5:7b"), ("remote_host", "https://ollama.com")):
            body = tags_body()
            body["models"][0][key] = value
            with self.subTest(key=key), self.assertRaises(SolverError):
                self.check_body(body)

    def test_invalid_model_inventory_is_rejected(self):
        for body in (None, {}, {"models": None}, {"models": "qwen2.5:7b"}, {"models": [None]}):
            with self.subTest(body=body), self.assertRaises(SolverError):
                self.check_body(body)

    def test_model_inventory_read_errors_are_safe(self):
        for error in (URLError("private connection details"), TimeoutError("private data")):
            with patch("question_volume.local_solver._open_local", side_effect=error) as mocked:
                with self.assertRaises(SolverError) as caught:
                    self.solver.check_model()
                self.assertNotIn("private", str(caught.exception))
                mocked.assert_called_once()

    def test_model_inventory_invalid_json_and_size_are_rejected(self):
        for raw in (b"not json", b"\xff", b" " * (256 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(SolverError):
                self.check_body(raw)

    def test_cloud_models_are_rejected_before_any_request(self):
        with patch("question_volume.local_solver._open_local") as mocked:
            for model in ("qwen3:cloud", "gpt-oss:120b-cloud", "gpt-oss:120b-cloud:cloud"):
                with self.subTest(model=model), self.assertRaises(SolverError):
                    OllamaAnswerSolver(model=model)
            mocked.assert_not_called()

    def test_invalid_configuration_fails_early(self):
        for model in ("", " ", None, "http://remote.example/model", "qwen2.5:7b\n"):
            with self.subTest(model=model), self.assertRaises(SolverError):
                OllamaAnswerSolver(model=model)
        for timeout in (0, -1, True, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(SolverError):
                OllamaAnswerSolver(timeout=timeout)


class LocalHttpIntegrationTests(unittest.TestCase):
    def test_redirects_are_not_followed(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/must-not-be-requested")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = "http://127.0.0.1:%d" % server.server_address[1]
            with patch.object(local_solver, "_LOCAL_ENDPOINT", endpoint):
                with self.assertRaises(SolverError):
                    OllamaAnswerSolver(timeout=3).check_model()
            self.assertEqual(requests, ["/api/tags"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_real_loopback_requests_bypass_environment_proxies(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def write_json(self, body):
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                requests.append(("GET", self.path, dict(self.headers), None))
                self.write_json(tags_body())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(("POST", self.path, dict(self.headers), body))
                self.write_json(response_body())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = "http://127.0.0.1:%d" % server.server_address[1]
            proxies = {
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "http_proxy": "http://127.0.0.1:1",
                "https_proxy": "http://127.0.0.1:1",
                "NO_PROXY": "",
                "no_proxy": "",
            }
            with patch.object(local_solver, "_LOCAL_ENDPOINT", endpoint), patch.dict(os.environ, proxies):
                solver = OllamaAnswerSolver(model="qwen2.5:7b", timeout=3)
                self.assertIsNone(solver.check_model())
                question = Question("What is 2 + 2?", (Choice("A", "3"), Choice("B", "4")))
                self.assertEqual(solver.solve(question), "B")
            self.assertEqual([(method, path) for method, path, _, _ in requests], [
                ("GET", "/api/tags"), ("POST", "/api/chat"),
            ])
            for _, _, headers, _ in requests:
                self.assertNotIn("authorization", {key.lower() for key in headers})
            self.assertEqual(requests[1][3]["model"], "qwen2.5:7b")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
