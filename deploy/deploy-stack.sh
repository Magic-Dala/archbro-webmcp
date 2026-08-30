#!/usr/bin/env bash
# Deploys one ArchBro stack on the VM. Invoked by .github/workflows/deploy.yml.
#
# Lives in its own file rather than inline in the workflow so it has one level
# of quoting instead of three, and so it can be run by hand while debugging.
#
# The registry token arrives on stdin, never as an argument, so it stays out of
# the process list. It is a short-lived Google OAuth access token from WIF.
# Docker uses a per-deploy temporary config that is destroyed on every exit path.
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

# Remote dev/main are production-like stacks. Never permit them to fall back to
# the local principal or non-production runtime safety semantics.
if ! sudo grep -qx 'ARCHBRO_ENV=production' "$DIR/.env"; then
    echo "::error::$DIR/.env must set ARCHBRO_ENV=production"
    exit 1
fi
if ! sudo grep -qx 'ARCHBRO_AUTH_MODE=firebase' "$DIR/.env"; then
    echo "::error::$DIR/.env must set ARCHBRO_AUTH_MODE=firebase"
    exit 1
fi

sudo mv "$HOME/archbro-$STACK.yml" "$DIR/docker-compose.yml"
sudo chmod 640 "$DIR/docker-compose.yml"

DOCKER_CONFIG_DIR="$(mktemp -d)"
cleanup_registry_auth() {
    sudo env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" docker logout "$REGISTRY" > /dev/null 2>&1 || true
    sudo rm -rf "$DOCKER_CONFIG_DIR"
}
trap cleanup_registry_auth EXIT

sudo env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" \
    docker login -u "$REGISTRY_USER" --password-stdin "$REGISTRY" > /dev/null

# --project-directory rather than cd: the calling shell is not root and cannot
# enter the directory at all.
compose() {
    sudo env DOCKER_CONFIG="$DOCKER_CONFIG_DIR" ARCHBRO_IMAGE="$IMAGE" ARCHBRO_STACK="$STACK" \
        docker compose -f "$DIR/docker-compose.yml" --project-directory "$DIR" -p "$STACK" "$@"
}

compose pull
compose up -d --wait

echo "--- $STACK ---"
compose ps --format 'table {{.Service}}\t{{.Status}}'
