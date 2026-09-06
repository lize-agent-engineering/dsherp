#!/bin/sh
# Development only. The dev stack runs the upstream image, which has no /home/frappe/backups
# or /home/frappe/backup-secrets, so Docker creates those mount points owned by root. The
# release image (infra/docker/frappe/Dockerfile) pre-creates them owned by the bench user, so
# production needs nothing. Run this once after adding the volumes.
set -eu
for container in dsherp-validation-backend-1 dsherp-validation-platform-backend-1; do
    docker exec -u root "$container" install -d -m 755 -o frappe -g frappe /home/frappe/backups
    docker exec -u root "$container" install -d -m 700 -o frappe -g frappe /home/frappe/backup-secrets
    docker exec "$container" sh -c 'test -w /home/frappe/backups && test -w /home/frappe/backup-secrets'
    echo "$container: staging directories ready"
done
