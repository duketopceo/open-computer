"""In-VM relay: owns one agent-harness process and exposes its stdio through files.

execplane `exec(cmd)` is request/response with no stdin or streaming, so the host cannot hold a pipe to a
process in the VM. This script runs INSIDE the VM (stdlib only, python3 >= 3.8, no imports from this repo) and
the host drives it with short execs:

  serve DIR CWD ARGV...   start the harness (env from DIR/env.json), pump DIR/in -> stdin and stdout/stderr ->
                          DIR/out, DIR/err; write DIR/ready, DIR/relay.pid; write DIR/rc when the harness is gone
  send  DIR B64           append the decoded bytes to DIR/in
  poll  DIR OUT_OFF ERR_OFF MAX   print one JSON line: new out/err bytes (base64) past the offsets, rc, liveness
  close DIR               touch DIR/close: the relay kills the harness process group and exits

The host treats everything this prints as untrusted input (see services/agents/sandbox.py).
"""

import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time


def _read_from(path, off, limit):
    try:
        with open(path, "rb") as f:
            f.seek(off)
            return f.read(limit)
    except OSError:
        return b""


def _size(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _read_text(path):
    with open(path) as f:
        return f.read()


def _append(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def serve(d, cwd, argv):
    os.makedirs(d, exist_ok=True)
    env = {}
    try:
        with open(os.path.join(d, "env.json")) as f:
            env = {str(k): str(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        pass
    _write_atomic(os.path.join(d, "relay.pid"), str(os.getpid()))
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    stop = threading.Event()

    def kill_group():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass

    def watch_close():  # its own thread: pump_in can block writing to a harness that stopped reading
        while not stop.is_set():
            if os.path.exists(os.path.join(d, "close")):
                kill_group()
                return
            time.sleep(0.05)

    def pump_in():
        off, path = 0, os.path.join(d, "in")
        while not stop.is_set():
            chunk = _read_from(path, off, 1 << 20) if _size(path) > off else b""
            if chunk:
                off += len(chunk)
                try:
                    proc.stdin.write(chunk)
                    proc.stdin.flush()
                except (OSError, ValueError):
                    return
            else:
                time.sleep(0.02)

    def pump_out(stream, name):
        path = os.path.join(d, name)
        while True:
            chunk = os.read(stream.fileno(), 65536)
            if not chunk:
                return
            _append(path, chunk)

    threads = [threading.Thread(target=pump_in, daemon=True),
               threading.Thread(target=pump_out, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump_out, args=(proc.stderr, "err"), daemon=True),
               threading.Thread(target=watch_close, daemon=True)]
    for t in threads:
        t.start()
    _write_atomic(os.path.join(d, "ready"), "1")
    rc = proc.wait()
    for t in threads[1:3]:  # drain: rc is written only after every output byte is on disk
        t.join(10)
    stop.set()
    kill_group()  # grandchildren must not outlive the harness
    _write_atomic(os.path.join(d, "rc"), str(rc))
    return 0


def _alive(d):
    try:
        pid = int(_read_text(os.path.join(d, "relay.pid")))
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    try:  # a zombie is dead
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def poll(d, out_off, err_off, limit):
    rc = None
    try:
        rc = int(_read_text(os.path.join(d, "rc")))  # read rc FIRST: once it exists no more bytes will arrive
    except (OSError, ValueError):
        pass
    out = _read_from(os.path.join(d, "out"), out_off, limit)
    err = _read_from(os.path.join(d, "err"), err_off, limit)
    sys.stdout.write(json.dumps({"out": base64.b64encode(out).decode(), "err": base64.b64encode(err).decode(),
                      "out_n": out_off + len(out), "err_n": err_off + len(err), "rc": rc,
                      "alive": _alive(d), "ready": os.path.exists(os.path.join(d, "ready"))}) + "\n")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "serve" and len(argv) >= 5:
        return serve(argv[2], argv[3], argv[4:])
    if cmd == "send" and len(argv) == 4:
        _append(os.path.join(argv[2], "in"), base64.b64decode(argv[3]))
        return 0
    if cmd == "poll" and len(argv) == 6:
        return poll(argv[2], int(argv[3]), int(argv[4]), int(argv[5]))
    if cmd == "close" and len(argv) == 3:
        _append(os.path.join(argv[2], "close"), b"1")
        return 0
    sys.stderr.write("usage: relay.py serve|send|poll|close ...\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
