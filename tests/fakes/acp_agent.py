"""Scripted fake ACP agent (adapted from duketopceo/wisp PR #241) (stdlib only, protocol v1 over stdio).

Run as ``python acp_agent.py [any argv...]`` (so it accepts being
invoked as ``ori opencode acp``). Behaviour comes from the JSON script
at ``$OC_FAKE_ACP_SCRIPT``; every start and every received message is
appended to ``$OC_FAKE_ACP_LOG`` (one JSON line each).

Script keys (all optional):
  protocolVersion  int, answered by ``initialize`` (default 1)
  models           model ids offered in the ``model`` config option
  stderr_bytes     bytes written to stderr at startup
  crash_on_start   exit code to die with before reading anything
  initialize_delay seconds to wait before answering ``initialize``
  omit_session_id  true: ``session/new`` replies without a sessionId
  prompts          list of step lists, one per ``session/prompt`` call
                   (the last list repeats); ``prompt`` is the one-list form
Steps: {"update": {...}} | {"permission": {"toolCall":.., "options":..}}
| {"delay": s} | {"wait_cancel": true} | {"stderr": n} | {"huge_line": n}
| {"crash": code} | {"stop": reason}. A cancel (or a cancelled permission
outcome) ends the prompt with stopReason "cancelled".

``install(bindir, script)`` installs this as ``ori`` on a BinDir.
"""
import json
import os
import pathlib
import queue
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve()
SCRIPT_ENV = "OC_FAKE_ACP_SCRIPT"
LOG_ENV = "OC_FAKE_ACP_LOG"
DEFAULT_OPTIONS = [
    {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
    {"optionId": "reject", "name": "Reject once", "kind": "reject_once"},
]


def log_rows(log) -> list:
    p = pathlib.Path(log)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines()
            if ln.strip()]


class Agent:
    def __init__(self):
        sp = os.environ.get(SCRIPT_ENV)
        self.script = json.loads(pathlib.Path(sp).read_text()) if sp else {}
        self.log_path = os.environ.get(LOG_ENV)
        self.inbox: queue.Queue = queue.Queue()
        self.models = self.script.get("models") or ["fake/model-a"]
        self.current_model = self.models[0]
        self.sessions = 0
        self.prompt_n = 0
        self.cancelled = False
        self.next_id = 1000

    def log(self, kind, **kw):
        if not self.log_path:
            return
        fd = os.open(self.log_path,
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps({"kind": kind, **kw}) + "\n").encode())
        finally:
            os.close(fd)

    def send(self, obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def reply(self, rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        self.send(msg)

    def update(self, sid, upd):
        self.send({"jsonrpc": "2.0", "method": "session/update",
                   "params": {"sessionId": sid, "update": upd}})

    def config_options(self):
        return [{"id": "model", "name": "Model", "category": "model",
                 "type": "select", "currentValue": self.current_model,
                 "options": [{"value": m, "name": m}
                             for m in self.models]}]

    def reader(self):
        for line in sys.stdin:
            if line.strip():
                self.inbox.put(json.loads(line))
        self.inbox.put(None)

    def get(self, timeout=None):
        try:
            msg = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return "timeout"
        if msg is None:
            sys.exit(0)
        self.log("recv", msg=msg)
        if msg.get("method") == "session/cancel":
            self.cancelled = True
        return msg

    def run_prompt(self, rid, sid):
        s = self.script
        lists = s.get("prompts") or [s.get("prompt") or []]
        steps = lists[min(self.prompt_n, len(lists) - 1)]
        self.prompt_n += 1
        self.cancelled = False
        stop = "end_turn"
        for st in steps:
            if self.cancelled:
                break
            if "update" in st:
                self.update(sid, st["update"])
            elif "permission" in st:
                self.next_id += 1
                pid = self.next_id
                p = st["permission"]
                self.send({"jsonrpc": "2.0", "id": pid,
                           "method": "session/request_permission",
                           "params": {"sessionId": sid,
                                      "toolCall": p.get("toolCall", {}),
                                      "options": p.get(
                                          "options", DEFAULT_OPTIONS)}})
                while True:
                    m = self.get()
                    if m.get("id") == pid and "method" not in m:
                        out = (m.get("result") or {}).get("outcome", {})
                        self.log("permission_outcome", outcome=out)
                        if out.get("outcome") == "cancelled":
                            self.cancelled = True
                        break
            elif "delay" in st:
                end = time.time() + st["delay"]
                while not self.cancelled and time.time() < end:
                    self.get(timeout=max(0.01, end - time.time()))
            elif st.get("wait_cancel"):
                while not self.cancelled:
                    self.get()
            elif "stderr" in st:
                sys.stderr.write("e" * st["stderr"])
                sys.stderr.flush()
            elif "huge_line" in st:
                sys.stdout.write("x" * st["huge_line"] + "\n")
                sys.stdout.flush()
            elif "crash" in st:
                os._exit(st["crash"])
            elif "stop" in st:
                stop = st["stop"]
        if self.cancelled:
            stop = "cancelled"
        self.reply(rid, {"stopReason": stop})

    def handle(self, msg):
        method, rid = msg.get("method"), msg.get("id")
        p = msg.get("params") or {}
        if method == "initialize":
            time.sleep(self.script.get("initialize_delay", 0))
            self.reply(rid, {
                "protocolVersion": self.script.get("protocolVersion", 1),
                "agentCapabilities": {"loadSession": True}})
        elif method == "session/new":
            self.sessions += 1
            res = {"sessionId": f"sess-{self.sessions}",
                   "configOptions": self.config_options()}
            if self.script.get("omit_session_id"):
                del res["sessionId"]
            self.reply(rid, res)
        elif method == "session/load":
            self.reply(rid, {"configOptions": self.config_options()})
        elif method == "session/set_config_option":
            if p.get("value") not in self.models:
                self.reply(rid, error={"code": -32602,
                                       "message": "unknown value"})
            else:
                self.current_model = p["value"]
                self.reply(rid, {"configOptions": self.config_options()})
        elif method == "session/prompt":
            self.run_prompt(rid, p.get("sessionId"))
        elif rid is not None:
            self.reply(rid, error={"code": -32601,
                                   "message": "method not found"})

    def main(self):
        self.log("start", argv=sys.argv[1:], env_keys=sorted(os.environ),
                 cwd=os.getcwd())
        if "crash_on_start" in self.script:
            os._exit(self.script["crash_on_start"])
        n = self.script.get("stderr_bytes")
        if n:
            sys.stderr.write("e" * n)
            sys.stderr.flush()
        threading.Thread(target=self.reader, daemon=True).start()
        while True:
            msg = self.get()
            self.handle(msg)


if __name__ == "__main__":
    Agent().main()
