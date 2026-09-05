#!/bin/sh
# Render the single allowed upstream, then serve. Fails before nginx starts when unset.
set -eu
: "${DSHERP_PROVIDER_HOST:?agent egress needs one provider host}"
envsubst '${DSHERP_PROVIDER_HOST}' < /opt/dsherp-agent-egress.conf > /etc/nginx/conf.d/agent-egress.conf
rm -f /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/frappe.conf
exec nginx -g 'daemon off;'
