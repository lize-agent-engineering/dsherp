# syntax=docker/dockerfile:1
# Site image: the pinned upstream base plus this repository's two Frappe Apps.
# Built from a repository tag; a production Site never mounts a working copy.
ARG DSHERP_BASE_IMAGE=frappe/erpnext@sha256:493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd
FROM ${DSHERP_BASE_IMAGE}

ARG DSHERP_IMAGE_TAG=dev
ARG DSHERP_GIT_COMMIT=unknown
LABEL org.opencontainers.image.title="dsherp-frappe" \
      org.opencontainers.image.version="${DSHERP_IMAGE_TAG}" \
      org.opencontainers.image.revision="${DSHERP_GIT_COMMIT}"

USER root
COPY --chown=frappe:frappe frappe_app /opt/dsherp-frappe
# The same headers (including CSP) that development overlays by bind mount.
COPY infra/nginx/security-headers.conf /etc/nginx/snippets/security_headers.conf
# Desk loads /assets/dsherp_bridge/dist/*; the built bundle ships in the image, not on a mount.
RUN set -eu; \
    rm -rf /opt/dsherp-frappe/*/__pycache__ /opt/dsherp-frappe/*/*/__pycache__; \
    test -f /opt/dsherp-frappe/dsherp_bridge/public/dist/context-agent.js; \
    ln -sfn /opt/dsherp-frappe/dsherp_bridge/public /home/frappe/frappe-bench/assets/dsherp_bridge; \
    /home/frappe/frappe-bench/env/bin/python -m compileall -q /opt/dsherp-frappe

USER frappe
ENV PYTHONPATH=/opt/dsherp-frappe
