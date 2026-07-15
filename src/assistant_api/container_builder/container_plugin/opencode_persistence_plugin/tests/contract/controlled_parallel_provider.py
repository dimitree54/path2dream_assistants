from __future__ import annotations

import base64
import shlex
from textwrap import dedent

from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
)


PROVIDER_PORT = 18080
STATE_PATH = "/tmp/parallel-provider-state.json"


class ControlledParallelProviderPlugin:
    name = "controlled-parallel-provider"

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.append("python3")
        encoded = base64.b64encode(_PROVIDER_SOURCE.encode()).decode("ascii")
        image.run_commands.append(
            "python3 -c "
            + shlex.quote(
                "import base64;open('/opt/parallel-provider.py','wb').write("
                f"base64.b64decode({encoded!r}))"
            )
        )

    def configure_container(self, container: ContainerSpec) -> None:
        container.env["OPENAI_API_KEY"] = "test"
        container.env["OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS"] = "true"
        container.managed_processes.append(
            ContainerManagedProcess(
                name=self.name,
                command=["python3", "/opt/parallel-provider.py"],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                (
                    "attempts=0; until wget -q -T 2 -O - "
                    f"http://127.0.0.1:{PROVIDER_PORT}/health | grep -q healthy; do "
                    "attempts=$((attempts + 1)); "
                    "[ \"$attempts\" -lt 30 ] || exit 1; sleep 0.2; done"
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Controlled provider failed to start: {result.output}")


_PROVIDER_SOURCE = dedent(
    r'''
    import json
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    lock = threading.Condition()
    state = {"tasks_issued": 0, "child_started": 0, "child_released": 0,
             "barrier_passed": False}

    def save_state():
        Path("/tmp/parallel-provider-state.json").write_text(json.dumps(state, sort_keys=True))

    def completed(seq):
        return {"type": "response.completed", "sequence_number": seq, "response": {
            "incomplete_details": None, "service_tier": None,
            "usage": {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 0}}}}

    def text_events(text):
        item = "msg_controlled"
        return [
            {"type": "response.created", "sequence_number": 1,
             "response": {"id": "resp_controlled", "created_at": 0, "model": "gpt-4o-mini"}},
            {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0,
             "item": {"type": "message", "id": item}},
            {"type": "response.output_text.delta", "sequence_number": 3,
             "item_id": item, "delta": text, "logprobs": None},
            {"type": "response.output_item.done", "sequence_number": 4, "output_index": 0,
             "item": {"type": "message", "id": item}},
            completed(5),
        ]

    def task_events(index):
        events = [{"type": "response.created", "sequence_number": 1,
                   "response": {"id": "resp_tasks", "created_at": 0, "model": "gpt-4o-mini"}}]
        seq = 1
        item = f"fc_{index}"
        call = f"call_{index}"
        args = json.dumps({"description": f"audit {index}",
                           "prompt": f"Reply exactly CHILD_{index}",
                           "subagent_type": "general", "background": True})
        seq += 1
        events.append({"type": "response.output_item.added", "sequence_number": seq,
                       "output_index": 0, "item": {"type": "function_call", "id": item,
                       "call_id": call, "name": "task", "arguments": "", "status": "in_progress"}})
        seq += 1
        events.append({"type": "response.function_call_arguments.delta", "sequence_number": seq,
                       "output_index": 0, "item_id": item, "delta": args})
        seq += 1
        events.append({"type": "response.function_call_arguments.done", "sequence_number": seq,
                       "output_index": 0, "item_id": item, "arguments": args})
        seq += 1
        events.append({"type": "response.output_item.done", "sequence_number": seq,
                       "output_index": 0, "item": {"type": "function_call", "id": item,
                       "call_id": call, "name": "task", "arguments": args, "status": "completed"}})
        events.append(completed(seq + 1))
        return events

    def next_parent_events():
        with lock:
            if state["tasks_issued"] >= 5:
                return text_events("PARENT_PARALLEL_OK")
            index = state["tasks_issued"]
            state["tasks_issued"] += 1
            save_state()
            return task_events(index)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"healthy":true}'
            self.send_response(200); self.send_header("content-length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            raw = json.dumps(payload.get("input", []))
            if "Generate a title for this conversation" in json.dumps(payload):
                return self._events(text_events("Controlled title"))
            if "Reply exactly REOPENED_OK" in raw:
                return self._events(text_events("REOPENED_OK"))
            if "function_call_output" in raw:
                return self._events(next_parent_events())
            if "Reply exactly CHILD_" in raw:
                with lock:
                    state["child_started"] += 1; save_state()
                    deadline = time.monotonic() + 30
                    while state["child_started"] < 5 and time.monotonic() < deadline:
                        lock.wait(timeout=0.2)
                    if state["child_started"] < 5:
                        return self._error("parallel child barrier timed out")
                    state["barrier_passed"] = True
                    state["child_released"] += 1; save_state(); lock.notify_all()
                child = raw.split("Reply exactly CHILD_", 1)[1][0]
                return self._events(text_events(f"CHILD_{child}"))
            if "Launch exactly five" in raw:
                return self._events(next_parent_events())
            return self._events(text_events("CONTROLLED_OK"))

        def _events(self, events):
            body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            body += "data: [DONE]\n\n"
            encoded = body.encode()
            self.send_response(200); self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)

        def _error(self, message):
            encoded = message.encode(); self.send_response(500)
            self.send_header("content-length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    save_state()
    ThreadingHTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
    '''
).strip()
