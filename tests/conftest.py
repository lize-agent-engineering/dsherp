import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def runtime_processes(monkeypatch):
    """Record real children; never replace the SDK or runtime with a fake."""
    processes = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        proc = original(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    yield processes
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.fixture
def model_server():
    """Only the paid provider is simulated; inspect the actual wire request."""
    requests = []
    state = {"finish_reason": "stop", "content": "DSHERP_OK"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, reason in [({"role": "assistant", "content": state["content"]}, None), ({}, state["finish_reason"])]:
                chunk = {"id": "synthetic", "object": "chat.completion.chunk", "created": 0,
                         "model": "deepseek-v4-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = {"DEEPSEEK_API_KEY": "synthetic-not-a-credential", "DSH_MODEL": "deepseek-v4-flash",
                "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"}
    yield settings, requests, state
    server.shutdown()
    server.server_close()
    thread.join()
