"""One synthetic, tool-free SDK turn. Not a tenant execution environment."""

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from deepseek_harness import DeepSeekHarness, RunResult
from deepseek_harness.models import Notification


CONFIG = Path(__file__).resolve().parents[1] / "config" / "dsh-no-tools.yml"


def run_probe(
    settings: Mapping[str, str],
    root: Path,
    on_notification: Callable[[Notification], None] | None = None,
) -> RunResult:
    for name in ("DEEPSEEK_API_KEY", "DSH_MODEL", "DEEPSEEK_BASE_URL"):
        if not settings.get(name, "").strip():
            raise ValueError(f"{name} is required")
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="dsh-probe-", dir=root) as directory:
        harness = DeepSeekHarness(
            provider="deepseek-official",
            model=settings["DSH_MODEL"],
            api_key=settings["DEEPSEEK_API_KEY"],
            base_url=settings["DEEPSEEK_BASE_URL"],
            cwd=directory,
            runtime_cwd=directory,
            session_root=str(Path(directory) / "sessions"),
            cordis=str(CONFIG),
            max_tokens=64,
            request_timeout_seconds=30,
            shutdown_timeout_seconds=2,
        )
        try:
            result = harness.run("Reply exactly DSHERP_OK.", on_notification=on_notification)
            if result.finish_reason != "completed":
                raise RuntimeError("DSH probe did not complete")
            if result.final_response.strip() != "DSHERP_OK":
                raise RuntimeError("DSH probe returned an unexpected response")
            return result
        finally:
            harness.close()


def main() -> int:
    try:
        result = run_probe(os.environ, CONFIG.parents[1] / "work")
    except Exception as exc:
        missing = [name for name in ("DEEPSEEK_API_KEY", "DSH_MODEL", "DEEPSEEK_BASE_URL") if not os.environ.get(name, "").strip()]
        # Never echo provider errors, response bodies, or runtime diagnostics.
        detail = "missing " + ", ".join(missing) if missing else type(exc).__name__
        print(f"DSH probe failed: {detail}", file=sys.stderr)
        return 1
    print(json.dumps({
        "finish_reason": result.finish_reason,
        "response_matches": result.final_response.strip() == "DSHERP_OK",
        "event_types": sorted({event["type"] for event in result.events}),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
