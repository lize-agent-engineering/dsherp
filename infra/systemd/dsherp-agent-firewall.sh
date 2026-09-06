#!/bin/sh
# Keep run containers on the agent network off the host itself.
#
# Docker's `internal: true` only drops FORWARD traffic; the bridge gateway (this host,
# with sshd and the worker's loopback port) stays reachable unless the INPUT chain says
# otherwise. These two rules did not survive a reboot, and a recreated agent network
# gets a new bridge name, so they are applied by dsherp-agent-firewall.service at boot
# and re-derived on every start. Rules carry a comment tag so `apply` can replace stale
# ones and `remove` deletes exactly what was added. iptables is used because Docker
# itself programs iptables on every host it runs on.
#
# Installed by root to /usr/local/sbin/dsherp-agent-firewall (see the runbook).
set -eu

TAG=dsherp-agent-firewall
STATE_DIR=/run/dsherp-agent-firewall

usage() { echo "usage: $0 apply|check|remove <agent network>" >&2; exit 64; }
[ $# -eq 2 ] || usage
action=$1
network=$2

bridge_of() {
    id=$(docker network inspect "$network" --format '{{.Id}}' 2>/dev/null) || return 1
    [ -n "$id" ] || return 1
    echo "br-$(printf %s "$id" | cut -c1-12)"
}

tagged_rules() {
    iptables -S INPUT | grep -F -- "--comment $TAG" || true
}

remove_rules() {
    tagged_rules | sed 's/^-A /-D /' | while read -r rule; do
        # shellcheck disable=SC2086
        iptables $rule
    done
}

case $action in
    apply)
        bridge=$(bridge_of) || { echo "$TAG: agent network $network not found; compose up first" >&2; exit 1; }
        remove_rules
        iptables -I INPUT 1 -i "$bridge" -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment "$TAG" -j ACCEPT
        iptables -I INPUT 2 -i "$bridge" -m comment --comment "$TAG" -j DROP
        mkdir -p "$STATE_DIR"
        printf '%s\n' "$bridge" > "$STATE_DIR/$network"
        chmod 644 "$STATE_DIR/$network"
        echo "$TAG: host INPUT drops $bridge ($network)"
        ;;
    check)
        bridge=$(bridge_of) || { echo "$TAG: agent network $network not found" >&2; exit 1; }
        if iptables -C INPUT -i "$bridge" -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment "$TAG" -j ACCEPT 2>/dev/null \
            && iptables -C INPUT -i "$bridge" -m comment --comment "$TAG" -j DROP 2>/dev/null \
            && [ "$(cat "$STATE_DIR/$network" 2>/dev/null)" = "$bridge" ]; then
            echo "$TAG: ok on $bridge ($network)"
            exit 0
        fi
        echo "$TAG: rules for $network on $bridge are missing or stale; run: systemctl restart dsherp-agent-firewall" >&2
        exit 1
        ;;
    remove)
        remove_rules
        rm -f "$STATE_DIR/$network"
        echo "$TAG: removed"
        ;;
    *)
        usage
        ;;
esac
