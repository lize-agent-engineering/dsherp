#!/bin/sh
set -eu

image='frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd'
volume='dsherp-v16probe_agent-runtime'
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

docker volume create "$volume" >/dev/null
docker run --rm --user 0:0 --entrypoint /bin/sh \
  -v "$volume:/opt/runtime" \
  -v "$root/requirements.lock:/opt/dsherp/requirements.lock:ro" \
  "$image" -eu -c '
    test ! -e /opt/runtime/bin/python
    python3 -m venv /opt/runtime
    /opt/runtime/bin/python -m pip install --require-hashes -r /opt/dsherp/requirements.lock
    /opt/runtime/bin/python -c "import deepseek_harness, mcp; print(\"C1-R14 PASS: Python 3.14 locked runtime imports\")"
  '
