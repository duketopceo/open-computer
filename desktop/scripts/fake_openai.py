#!/usr/bin/env python3
"""A fake OpenAI-compatible server for the real-binary harness checks. It never calls anything and needs no key.

Usage: fake_openai.py PORT_FILE [--slow]

Binds 127.0.0.1 on a free port, writes the port to PORT_FILE, and answers /v1/models and /v1/chat/completions with a fixed
reply (streamed when asked). With --slow, a chat request waits 60 seconds before answering, so a test can press Stop.
Every request line is appended to requests.log next to PORT_FILE (no bodies, no headers), so a test can prove the harness
really reached this server and nothing else.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ANSWER = "FAKE-ANSWER-OK"
SLOW = "--slow" in sys.argv
LOG = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence stderr
        pass

    def _note(self):
        if LOG:
            with open(LOG, "a") as f:
                f.write(f"{self.command} {self.path}\n")

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._note()
        if self.path.rstrip("/").endswith("/models"):
            self._json({"object": "list", "data": [{"id": "fake-model", "object": "model", "owned_by": "fake"}]})
        else:
            self._json({"ok": True})

    def do_POST(self):
        self._note()
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except ValueError:
            req = {}
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json({"error": {"message": "not found"}}, 404)
            return
        if SLOW:
            time.sleep(60)
        msgs = req.get("messages") or []
        want_tool = any(m.get("role") == "user" and "USE-TOOL" in json.dumps(m.get("content")) for m in msgs) and not any(m.get("role") == "tool" for m in msgs)
        tool_name = next((t["function"]["name"] for t in (req.get("tools") or []) if t.get("function", {}).get("name") in ("bash", "terminal")), None)
        base = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": req.get("model", "fake-model")}
        if req.get("stream") and want_tool and tool_name:
            # One tool call, then the harness comes back with the result and gets the plain answer.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            call = {"index": 0, "id": "call_fake_1", "type": "function", "function": {"name": tool_name, "arguments": json.dumps({"command": "echo tool-ran", "description": "echo"})}}
            for c in [
                {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": None, "tool_calls": [call]}, "finish_reason": None}]},
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}},
            ]:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
        elif req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            pieces = [ANSWER[:5], ANSWER[5:]]
            chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}]
            chunks += [{**base, "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]} for p in pieces]
            chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True
        else:
            self._json({
                "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": req.get("model", "fake-model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            })


def main():
    global LOG
    port_file = Path(sys.argv[1])
    LOG = str(port_file.parent / "requests.log")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port_file.write_text(str(server.server_address[1]))
    server.serve_forever()


if __name__ == "__main__":
    main()
