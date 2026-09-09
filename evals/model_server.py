"""A scripted provider, mounted read-only inside the run container.

Same trick as `tests/conftest.py`'s `model_server` — the container reaches it on its own
loopback at 127.0.0.1:38127, so nothing about the egress isolation, the agent network or
`deployment_digest` changes (and `deployment_digest` is precisely one of the things this
plan measures, so changing it to run an evaluation would defeat the point).

Two differences from the test fixture, both deliberate:

1. The turns come from a **hand-written** JSON script, so a case can script a proposal call
   the exported `sources` could never have contained.
2. The SSE chunks **carry a usage**, so a replay can check that the provider's own token
   counts actually land in `DS Model Run` — the test fixture reports none, which is why a
   replayed run there always ends with `usage_unknown_calls == model_calls`.

stdlib only: this file is bind-mounted into the run container, which has no test packages.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 38127
MODEL = 'deepseek-v4-flash'
COMPACT_HEADER = 'x-deepseek-harness-compact'


def load_script(path):
    """`{'expected_verdict','turns':[...],'usage':{...}}`, checked enough to fail loudly here
    rather than as a mystery mid-run."""
    script = json.loads(Path(path).read_text(encoding='utf-8'))
    if script.get('expected_verdict') not in ('pass', 'fail'):
        raise ValueError(f'回放脚本必须声明 expected_verdict 为 pass 或 fail：{path}')
    turns = script.get('turns')
    if not isinstance(turns, list) or not turns:
        raise ValueError(f'回放脚本必须有非空 turns：{path}')
    for index, turn in enumerate(turns):
        if 'tool_call' in turn:
            call = turn['tool_call']
            if not isinstance(call, dict) or not call.get('name') or not isinstance(call.get('arguments'), str):
                raise ValueError(f'第 {index + 1} 轮的 tool_call 需要 name 与 JSON 字符串 arguments：{path}')
        elif not isinstance(turn.get('content'), str):
            raise ValueError(f'第 {index + 1} 轮既不是 tool_call 也没有 content：{path}')
    return script


def _chunk(delta, reason):
    payload = {'id': 'eval-replay', 'object': 'chat.completion.chunk', 'created': 0, 'model': MODEL,
               'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
    # ensure_ascii=False: a real provider sends UTF-8, and an answer whose Chinese text
    # arrives \u-escaped is not the same bytes the model would have produced.
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


def wire_usage(usage):
    """The provider's own key names. A script may write either the OpenAI names or the plain
    ones; both go out as `prompt_tokens`/`completion_tokens`/`total_tokens`, because that is
    what the client's usage parser reads. Send anything else and the fields it did not find
    stay undefined, the chunk it builds is not JSON-serializable, and the harness kills the
    turn with `session event "assistant/chunk" carries non-JSON-serializable data` — which
    looks nothing like a usage problem."""
    given = usage or {}
    prompt = int(given.get('prompt_tokens', given.get('input_tokens', 0)) or 0)
    completion = int(given.get('completion_tokens', given.get('output_tokens', 0)) or 0)
    return {'prompt_tokens': prompt, 'completion_tokens': completion,
            'total_tokens': prompt + completion}


def _usage_chunk(usage):
    """Usage rides its own trailing chunk with an empty `choices`, the way OpenAI-compatible
    providers send it under `stream_options.include_usage`."""
    payload = {'id': 'eval-replay', 'object': 'chat.completion.chunk', 'created': 0, 'model': MODEL,
               'choices': [], 'usage': wire_usage(usage)}
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


def serve(script_path, port=PORT):
    """Start the scripted provider. Returns (settings, requests, state); call
    `state['shutdown']()` to stop it."""
    script = load_script(script_path)
    turns = script['turns']
    usage = script.get('usage') or {'input_tokens': 0, 'output_tokens': 0}
    requests = []
    state = {'script': script, 'compaction_calls': []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length'] or 0))
            requests.append(json.loads(body or b'{}'))
            compact = self.headers.get(COMPACT_HEADER) == '1'
            state['compaction_calls'].append(compact)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            # A compaction request is the harness asking for a summary of the conversation so
            # far. Replaying a scripted tool call into it would put the call in the wrong
            # place and shift every later turn, so compaction always gets plain text.
            index = sum(1 for was_compact in state['compaction_calls'][:-1] if not was_compact)
            turn = {'content': '（回放压缩摘要）'} if compact else (
                turns[index] if index < len(turns) else turns[-1])
            if not compact and 'tool_call' in turn:
                call = turn['tool_call']
                deltas = [({'role': 'assistant', 'tool_calls': [
                    {'index': 0, 'id': f'call-eval-{len(requests)}', 'type': 'function',
                     'function': {'name': call['name'], 'arguments': call['arguments']}}]}, None),
                    ({}, 'tool_calls')]
            else:
                deltas = [({'role': 'assistant', 'content': turn.get('content', '')}, None),
                          ({}, turn.get('finish_reason', 'stop'))]
            for delta, reason in deltas:
                self.wfile.write(_chunk(delta, reason).encode())
            self.wfile.write(_usage_chunk(usage).encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def shutdown():
        server.shutdown()
        server.server_close()
        thread.join()

    state['shutdown'] = shutdown
    settings = {'DEEPSEEK_API_KEY': 'synthetic-not-a-credential', 'DSH_MODEL': MODEL,
                'DEEPSEEK_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1'}
    return settings, requests, state
