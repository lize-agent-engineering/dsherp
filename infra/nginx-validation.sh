#!/bin/sh
set -eu
: "${DSHERP_PUBLIC_ORIGIN:?}"
: "${DSHERP_SECONDARY_ORIGIN:?}"
: "${DSHERP_ALLOWED_HOSTS:?}"
# Pinned image defaults, rendering only the local validation proxy configuration.
export UPSTREAM_REAL_IP_ADDRESS=127.0.0.1 UPSTREAM_REAL_IP_HEADER=X-Forwarded-For UPSTREAM_REAL_IP_RECURSIVE=off
export PROXY_READ_TIMEOUT=120 CLIENT_MAX_BODY_SIZE=50m
envsubst '${BACKEND} ${SOCKETIO} ${FRAPPE_SITE_NAME_HEADER} ${UPSTREAM_REAL_IP_ADDRESS} ${UPSTREAM_REAL_IP_HEADER} ${UPSTREAM_REAL_IP_RECURSIVE} ${PROXY_READ_TIMEOUT} ${CLIENT_MAX_BODY_SIZE} ${DSHERP_PUBLIC_ORIGIN} ${DSHERP_SECONDARY_ORIGIN} ${DSHERP_ALLOWED_HOSTS}' < /opt/dsherp-frappe.conf.template > /etc/nginx/conf.d/frappe.conf
exec nginx -g 'daemon off;'
