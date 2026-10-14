"""Answer using a downloaded Ollama model on this computer, without cloud access."""

import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .models import Question
from .solver import SolverError, _INSTRUCTIONS, _unique_object

_LOCAL_ENDPOINT = "http://127.0.0.1:11434"
_MAX_RESPONSE_BYTES = 256 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def _open_local(request, timeout):
    # Never route screen text through an HTTP proxy or follow redirects off loopback.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


class OllamaAnswerSolver:
    def __init__(self, model="qwen2.5:7b", timeout=180):
        if (not isinstance(model, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?", model) is None):
            raise SolverError("Configure a downloaded Ollama model, such as qwen2.5:7b.")
        if model.lower().endswith("cloud"):
            raise SolverError("Cloud models are not supported by the local solver. Use qwen2.5:7b.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise SolverError("The local model timeout must be a positive finite number.")
        self.model = model
        self.timeout = float(timeout)

    @property
    def _model_names(self):
        return {self.model, self.model if ":" in self.model else self.model + ":latest"}

    def _request(self, path, payload=None):
        request = Request(
            _LOCAL_ENDPOINT + path,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with _open_local(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 404:
                raise SolverError(f"Ollama could not find the model or route. Run: ollama pull {self.model}") from None
            raise SolverError("Ollama rejected the request; check the running server and downloaded model.") from None
        except (URLError, TimeoutError, OSError):
            raise SolverError("Ollama could not complete the request. Open Ollama and check the model or timeout.") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise SolverError("Ollama returned a response that was too large.")
        try:
            result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeDecodeError):
            raise SolverError("Ollama returned an unreadable response.") from None
        if not isinstance(result, dict) or result.get("error"):
            raise SolverError("Ollama did not return a usable response.")
        return result

    def check_model(self):
        result = self._request("/api/tags")
        models = result.get("models")
        if not isinstance(models, list):
            raise SolverError("Ollama returned an invalid model list.")
        for model in models:
            if not isinstance(model, dict):
                continue
            names = (model.get("name"), model.get("model"))
            if any(isinstance(name, str) and name in self._model_names for name in names):
                if model.get("remote_host") or model.get("remote_model"):
                    raise SolverError("The selected Ollama model uses a remote service. Download qwen2.5:7b instead.")
                return
        raise SolverError(f"The local model is not downloaded. Run: ollama pull {self.model}")

    def solve(self, question: Question):
        labels = [choice.label for choice in question.choices]
        if (not question.prompt.strip() or len(labels) < 2
                or any(not isinstance(label, str) or len(label) != 1 or not "A" <= label <= "Z" for label in labels)
                or len(set(labels)) != len(labels)
                or any(not choice.text.strip() for choice in question.choices)):
            return None
        result = self._request("/api/chat", {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": _INSTRUCTIONS + ' Respond only with JSON: {"answer":"A"} or {"answer":null}.'},
                {"role": "user", "content": json.dumps({
                    "question": question.prompt,
                    "choices": [{"label": choice.label, "text": choice.text} for choice in question.choices],
                }, ensure_ascii=False)},
            ],
            "format": {
                "type": "object",
                "properties": {"answer": {"type": ["string", "null"], "enum": labels + [None]}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            "options": {"temperature": 0, "num_predict": 128, "num_ctx": 4096},
            "keep_alive": "5m",
        })
        message = result.get("message")
        if (result.get("done") is not True or not isinstance(result.get("model"), str)
                or result.get("model") not in self._model_names
                or not isinstance(message, dict) or message.get("role") != "assistant"
                or not isinstance(message.get("content"), str) or message.get("tool_calls")
                or result.get("done_reason") == "length"):
            raise SolverError("Ollama returned an incomplete or unsupported answer.")
        try:
            answer = json.loads(message["content"], object_pairs_hook=_unique_object)
        except ValueError:
            raise SolverError("Ollama returned an unreadable answer.") from None
        if not isinstance(answer, dict) or set(answer) != {"answer"}:
            raise SolverError("Ollama returned an invalid answer shape.")
        selected = answer["answer"]
        if selected is None:
            return None
        if not isinstance(selected, str) or selected not in labels:
            raise SolverError("Ollama returned an unknown answer choice.")
        return selected
