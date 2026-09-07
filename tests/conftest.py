import json
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

INTEGRATION = Path(__file__).resolve().parent / "integration"


def pytest_ignore_collect(collection_path, config):
    """tests/integration is its own suite: it needs the four-Site stack, and it reuses unit-test
    file names (test_run_events.py, ...), so the two can never share one session. It is collected
    only when named on the command line: `python -m pytest tests/integration -m integration`."""
    if Path(collection_path).resolve() != INTEGRATION:
        return None
    base = Path(config.invocation_params.dir)
    requested = []
    for argument in config.args:
        path = (base / argument.split("::", 1)[0]).resolve()
        requested.append(path == INTEGRATION or INTEGRATION in path.parents)
    return not any(requested)


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
def model_server(port=0):
    """Only the paid provider is simulated; inspect the actual wire request."""
    requests = []
    state = {"finish_reason": "stop", "content": "DSHERP_OK"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            compact=self.headers.get('x-deepseek-harness-compact')=='1'
            state.setdefault('compaction_calls',[]).append(compact)
            if state.get('received'):
                state['received'].set()
            if state.get('release'):
                state['release'].wait(timeout=10)
                return  # Deliberately stalled synthetic request was cancelled.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            sequence=state.get('tool_calls',[state['tool_call']] if state.get('tool_call') else [])
            if not compact and len(requests)<=len(sequence):
                deltas = [({"role": "assistant", "tool_calls": [{"index": 0, "id": "call-erp-"+str(len(requests)), "type": "function", "function": sequence[len(requests)-1]}]}, None), ({}, "tool_calls")]
            else:
                content=state.get('summary_content',state['content']) if compact else state['content']
                deltas = [({"role": "assistant", "content": content}, None), ({}, state["finish_reason"])]
            for delta, reason in deltas:
                chunk = {"id": "synthetic", "object": "chat.completion.chunk", "created": 0,
                         "model": "deepseek-v4-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = {"DEEPSEEK_API_KEY": "synthetic-not-a-credential", "DSH_MODEL": "deepseek-v4-flash",
                "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"}
    yield settings, requests, state
    server.shutdown()
    server.server_close()
    thread.join()
