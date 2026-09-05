# syntax=docker/dockerfile:1
# Agent Runtime image: the hashed project lock plus the modules the run container needs.
# It carries no control-plane file, no compose definition and no credential.
ARG DSHERP_BASE_IMAGE=frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
FROM ${DSHERP_BASE_IMAGE}

ARG DSHERP_IMAGE_TAG=dev
ARG DSHERP_GIT_COMMIT=unknown
LABEL org.opencontainers.image.title="dsherp-worker" \
      org.opencontainers.image.version="${DSHERP_IMAGE_TAG}" \
      org.opencontainers.image.revision="${DSHERP_GIT_COMMIT}"

USER root
COPY requirements.lock /run/requirements.lock
RUN set -eu; \
    python3 -m venv /opt/runtime; \
    /opt/runtime/bin/pip install --no-cache-dir --require-hashes -r /run/requirements.lock; \
    /opt/runtime/bin/python -c "from deepseek_harness import DeepSeekHarness; import mcp, httpx"; \
    rm -f /run/requirements.lock

COPY dsherp /opt/dsherp/dsherp
COPY config /opt/dsherp/config
COPY runtime /opt/dsherp/runtime
COPY business-skills /opt/dsherp/business-skills
COPY requirements.lock /opt/dsherp/requirements.lock
RUN set -eu; \
    rm -rf /opt/dsherp/dsherp/__pycache__; \
    chmod -R a=rX /opt/dsherp /opt/runtime; \
    chmod -R a+x /opt/runtime/bin

ENV PYTHONPATH=/opt/dsherp PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /session
ENTRYPOINT ["/opt/runtime/bin/python", "-m", "dsherp.context_runner"]
