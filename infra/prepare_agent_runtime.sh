#!/bin/sh
# Reproduce the hashed project lock in a dedicated Linux volume; never pull images.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=frappe/erpnext@sha256:cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341
docker image inspect "$IMAGE" >/dev/null
docker volume create dsherp-agent-runtime >/dev/null
docker run --rm --pull=never --memory 384m --memory-swap 384m --cpus 0.1 \
  --user 0:0 --entrypoint /bin/sh -v dsherp-agent-runtime:/opt/runtime \
  -v "$ROOT/requirements.lock:/run/requirements.lock:ro" "$IMAGE" -ec '
  python3 -m venv /opt/runtime
  /opt/runtime/bin/pip install --no-cache-dir --require-hashes -r /run/requirements.lock
  /opt/runtime/bin/python -c "from deepseek_harness import DeepSeekHarness; import mcp, httpx"
'
