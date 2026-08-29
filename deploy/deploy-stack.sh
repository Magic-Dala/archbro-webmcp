#!/usr/bin/env bash
# Deploys one ArchBro stack on the VM. Invoked by .github/workflows/deploy.yml.
#
# Lives in its own file rather than inline in the workflow so it has one level
# of quoting instead of three, and so it can be run by hand while debugging.
#
# The registry token arrives on stdin, never as an argument, so it stays out of
# the process list. It is the workflow's GITHUB_TOKEN, which expires when the
# job ends, and the script logs out before it finishes either way.
#
# Every docker command runs under sudo: /opt/archbro is root-only so that the
# .env files, which hold the secrets, are not world-readable.
set -euo pipefail

STACK="${1:?stack name required, e.g. archbro-main}"
DIR="${2:?target directory required, e.g. /opt/archbro/main}"
IMAGE="${3:?image reference required}"
REGISTRY="${4:?registry host required}"
REGISTRY_USER="${5:?registry username required}"

if ! sudo test -f "$DIR/.env"; then
    echo "::error::$DIR/.env is missing. Place it on the VM by hand before deploying."
    exit 1
fi

# A deployment whose .env forgot ARCHBRO_ENV would fall back to the development
# default and serve real traffic on the local development principal.
if ! sudo grep -q '^ARCHBRO_ENV=' "$DIR/.env"; then
    echo "::error::$DIR/.env does not set ARCHBRO_ENV"
    exit 1
fi
if ! sudo grep -q '^ARCHBRO_ENV=production' "$DIR/.env"; then
    echo "::warning::$DIR/.env does not set ARCHBRO_ENV=production; Firebase authentication is not enforced"
fi

sudo mv "/tmp/archbro-$STACK.yml" "$DIR/docker-compose.yml"
sudo chmod 640 "$DIR/docker-compose.yml"

sudo docker login -u "$REGISTRY_USER" --password-stdin "$REGISTRY" > /dev/null

# --project-directory rather than cd: the calling shell is not root and cannot
# enter the directory at all.
compose() {
    sudo env ARCHBRO_IMAGE="$IMAGE" ARCHBRO_STACK="$STACK" \
        docker compose -f "$DIR/docker-compose.yml" --project-directory "$DIR" -p "$STACK" "$@"
}

compose pull
compose up -d --wait

sudo docker logout "$REGISTRY" > /dev/null

echo "--- $STACK ---"
compose ps --format 'table {{.Service}}\t{{.Status}}'
