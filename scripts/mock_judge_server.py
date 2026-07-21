"""
Mock LLM judge server for offline FlockMTL testing.

Run: python scripts/mock_judge_server.py
Then set: OPENAI_BASE_URL=http://localhost:11499/v1

FlockMTL will route llm_complete/llm_filter calls through this
server instead of hitting a real LLM API. Returns deterministic
responses based on simple keyword matching — useful for testing
prompt wiring and batching without burning API credits.

RESPONSE SHAPE — verified against flock source:
  - OpenAI handler's ExtractCompletionOutput calls
    nlohmann::json::parse(choice["message"]["content"]).
  - Then Complete() returns response[0]["items"].
  - Therefore the assistant content must be a JSON object that
    contains an "items" key, and each item is the per-row verdict.
  - We return {"items": [{"verdict": "<scalar>"}]} so the verdict
    lands as the first items element.
"""

import json
import random
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 11499

KEYWORD_SCORES = {
    "laptop": 3,
    "programming": 3,
    "developer": 3,
    "tutorial": 2,
    "guide": 2,
    "review": 2,
    "comparison": 2,
    "forum": 1,
    "reddit": 1,
    "ad": 0,
    "sponsored": 0,
}


class MockJudgeHandler(BaseHTTPRequestHandler):
    def _score_relevance(self, prompt_text: str) -> int:
        """Score 0-3 based on keyword overlap with developer query intent."""
        text_lower = prompt_text.lower()
        best = 0
        for keyword, score in KEYWORD_SCORES.items():
            if keyword in text_lower and score > best:
                best = score
        return best if best > 0 else random.randint(0, 2)

    def _classify_failure(self, prompt_text: str) -> str:
        text = prompt_text.lower()
        if "timeout" in text or "timed out" in text:
            return "provider_timeout"
        if "empty" in text or "no results" in text:
            return "no_results"
        if "rerank" in text or "dropped" in text:
            return "rerank_error"
        if "off-topic" in text or "irrelevant" in text:
            return "irrelevant_sources"
        return random.choice(["no_results", "irrelevant_sources", "other"])

    def _verdict_for_prompt(self, prompt_text: str) -> str:
        """Return the raw verdict string for the given prompt text."""
        p = prompt_text.lower()
        if "classify" in p or "root cause" in p:
            return self._classify_failure(prompt_text)
        if "0-3 scale" in p or "score this" in p:
            # judge_query_rewrite prompt — return score + 1-line reason
            return f"{self._score_relevance(prompt_text)}\nScore heuristic based on keyword overlap"
        if "semantically" in p or "duplicate" in p:
            return "YES" if random.random() < 0.3 else "NO"
        # Default: grade_relevance shape (scalar 0-3)
        return str(self._score_relevance(prompt_text))

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}

        # Handle batch requests (FlockMTL sends arrays)
        messages = body.get("messages", [body])
        if not isinstance(messages, list):
            messages = [messages]

        choices = []
        for msg in messages:
            prompt = ""
            if isinstance(msg, dict):
                user_msgs = [m for m in msg.get("messages", []) if m.get("role") == "user"]
                prompt = user_msgs[-1]["content"] if user_msgs else ""
            else:
                prompt = str(msg)

            verdict = self._verdict_for_prompt(prompt)
            response_obj = {"items": [{"verdict": verdict}]}
            choices.append(
                {
                    "index": len(choices),
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(response_obj),
                    },
                    "finish_reason": "stop",
                }
            )

        response_body = {
            "id": f"mock-judge-{random.randint(1000, 9999)}",
            "object": "chat.completion",
            "created": 0,
            "model": "mock-judge-1.0",
            "choices": choices,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response_body).encode())

    def log_message(self, format, *args):
        pass  # suppress default logging


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), MockJudgeHandler)
    print(f"Mock judge server running on http://localhost:{PORT}/v1")
    print("Set OPENAI_BASE_URL=http://localhost:11499/v1 in your DuckDB session")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down mock judge server")
        server.shutdown()
