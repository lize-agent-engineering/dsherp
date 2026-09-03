"""Print the event and notification shapes emitted by the pinned runtime."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsherp.session_runtime import open_runtime
from tests.conftest import model_server as _fixture


def shape(value, depth=0):
    if isinstance(value, dict):
        if depth >= 5:
            return "dict"
        return {key: shape(item, depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def main(directory):
    generator = _fixture.__wrapped__() if hasattr(_fixture, "__wrapped__") else None
    if generator is None:
        raise SystemExit("conftest model_server fixture must be a plain generator function")
    settings, _requests, state = next(generator)
    state["tool_call"] = {
        "name": "skill",
        "arguments": json.dumps({"name": "erp-query"}),
    }
    notifications = []
    try:
        with open_runtime(
            settings,
            Path(directory),
            "probe-events",
            resume=False,
        ) as runtime:
            result = runtime.run(
                "probe",
                session_id="probe-events",
                on_notification=notifications.append,
            )
        types = sorted({event.get("type") for event in result.events})
        print(
            json.dumps(
                {
                    "event_types": types,
                    "shapes": {
                        event_type: shape(
                            next(
                                event
                                for event in result.events
                                if event.get("type") == event_type
                            )
                        )
                        for event_type in types
                    },
                    "notification_methods": sorted(
                        {notification.method for notification in notifications}
                    ),
                    "finish_reason": result.finish_reason,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


if __name__ == "__main__":
    main(sys.argv[1])
