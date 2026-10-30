"""A tiny in-process OpenRouter: records every request, answers from a script. For client/wiring tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOpenRouter:
    def __init__(self):
        self.requests: list[dict] = []
        self.chat_replies: list = []  # dicts -> 200 JSON; ints -> that HTTP status; "garbage" -> non-JSON 200
        self.generations: dict[str, dict] = {}
        self.generation_404s = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                reply = outer.chat_replies.pop(0) if outer.chat_replies else outer.default_reply(body)
                if isinstance(reply, int):
                    self._send(reply, {"error": {"code": reply, "message": "scripted failure sk-or-v1-leakleakleakleak12"}})
                elif reply == "garbage":
                    self._send(200, b"<html>not json</html>", "text/html")
                else:
                    self._send(200, reply)

            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": None})
                gid = self.path.split("id=")[-1]
                if outer.generation_404s > 0:
                    outer.generation_404s -= 1
                    return self._send(404, {"error": {"code": 404, "message": "not found"}})
                if gid in outer.generations:
                    return self._send(200, {"data": outer.generations[gid]})
                self._send(404, {"error": {"code": 404, "message": "not found"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1"
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    @staticmethod
    def default_reply(body, cost=0.0012):
        return chat_reply("ok", cost=cost, model=body.get("model", "m"))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()


_n = [0]


def chat_reply(text="ok", *, cost=0.0012, model="vendor/model", gid=None, tokens_in=10, tokens_out=5, provider="Acme",
               with_cost=True, reasoning=None, reasoning_details=None):
    _n[0] += 1
    usage = {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "total_tokens": tokens_in + tokens_out,
             "prompt_tokens_details": {"cached_tokens": 2}, "completion_tokens_details": {"reasoning_tokens": 1}}
    if with_cost:
        usage["cost"] = cost
        usage["cost_details"] = {"upstream_inference_cost": None}
    message = {"role": "assistant", "content": text}
    if reasoning is not None:
        message["reasoning"] = reasoning
    if reasoning_details is not None:
        message["reasoning_details"] = reasoning_details
    return {"id": gid or f"gen-test{_n[0]:04d}", "model": model, "provider": provider,
            "choices": [{"message": message, "finish_reason": "stop"}], "usage": usage}
