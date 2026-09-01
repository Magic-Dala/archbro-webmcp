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
ENV_FILE="$DIR/.env"

if ! sudo test -f "$ENV_FILE"; then
    echo "::error::$ENV_FILE is missing. Place it on the VM by hand before deploying."
    exit 1
fi

contract_error() {
    echo "::error::$ENV_FILE $1" >&2
    exit 1
}

# Parse only the deployment-contract keys instead of sourcing .env as shell
# code. Duplicate assignments are rejected because Compose uses the last value,
# which can otherwise disagree with a grep that matched an earlier safe value.
# The narrow parser supports the dotenv forms used by Compose for these scalar
# values: whitespace around the assignment, CRLF, matching quotes, and unquoted
# inline comments.
read_env_value() {
    local key="$1"
    local required="${2:-1}"
    local parsed=""
    local status=0

    parsed="$(
        sudo awk -v wanted="$key" '
            function trim(value) {
                sub(/^[[:space:]]+/, "", value)
                sub(/[[:space:]]+$/, "", value)
                return value
            }

            BEGIN { single_quote = sprintf("%c", 39) }

            {
                line = $0
                sub(/\r$/, "", line)
                if (line ~ /^[[:space:]]*#/ || line ~ /^[[:space:]]*$/) {
                    next
                }

                separator = index(line, "=")
                if (!separator) {
                    next
                }

                name = trim(substr(line, 1, separator - 1))
                if (name != wanted) {
                    next
                }

                count++
                raw_value = substr(line, separator + 1)
                if (raw_value ~ /^[[:space:]]+#/) {
                    value = ""
                } else {
                    value = trim(raw_value)
                    first = substr(value, 1, 1)
                    if (first == "\"" || first == single_quote) {
                        closing_quote = 0
                        escaped = 0
                        for (position = 2; position <= length(value); position++) {
                            character = substr(value, position, 1)
                            if (first == "\"" && character == "\\" && !escaped) {
                                escaped = 1
                                continue
                            }
                            if (character == first && !escaped) {
                                closing_quote = position
                                break
                            }
                            escaped = 0
                        }

                        trailing = trim(substr(value, closing_quote + 1))
                        if (!closing_quote || (trailing != "" && trailing !~ /^#/)) {
                            invalid_quotes = 1
                        } else {
                            value = substr(value, 2, closing_quote - 2)
                        }
                    } else {
                        sub(/[[:space:]]+#.*$/, "", value)
                        value = trim(value)
                        if (value ~ /[\047\"]/) {
                            invalid_quotes = 1
                        }
                    }
                }
                parsed = value
            }

            END {
                if (count == 0) exit 10
                if (count > 1) exit 11
                if (invalid_quotes) exit 12
                printf "%s", parsed
            }
        ' "$ENV_FILE"
    )" || status=$?

    case "$status" in
        0) printf '%s' "$parsed" ;;
        10)
            if [ "$required" = "1" ]; then
                contract_error "must set $key exactly once"
            fi
            ;;
        11) contract_error "must set $key exactly once; duplicate assignments found" ;;
        12) contract_error "has invalid quoting for $key" ;;
        *) contract_error "could not parse $key" ;;
    esac
}

require_value() {
    local key="$1"
    local actual="$2"
    local expected="$3"
    if [ "$actual" != "$expected" ]; then
        contract_error "must set $key=$expected"
    fi
}

require_literal_nonempty() {
    local key="$1"
    local value="$2"
    if [ -z "$value" ] || [[ "$value" =~ [[:space:]] ]] || [[ "$value" == *'$'* ]]; then
        contract_error "must set non-empty literal $key"
    fi
}

ARCHBRO_ENV_VALUE="$(read_env_value ARCHBRO_ENV)"
ARCHBRO_AUTH_MODE_VALUE="$(read_env_value ARCHBRO_AUTH_MODE)"

require_production_firebase() {
    local firebase_project_id
    local google_cloud_project
    local browser_api_key
    local browser_auth_domain

    require_value ARCHBRO_ENV "$ARCHBRO_ENV_VALUE" production
    require_value ARCHBRO_AUTH_MODE "$ARCHBRO_AUTH_MODE_VALUE" firebase

    firebase_project_id="$(read_env_value FIREBASE_PROJECT_ID 0)"
    google_cloud_project="$(read_env_value GOOGLE_CLOUD_PROJECT 0)"
    if [ -n "$firebase_project_id" ]; then
        require_literal_nonempty FIREBASE_PROJECT_ID "$firebase_project_id"
    elif [ -n "$google_cloud_project" ]; then
        require_literal_nonempty GOOGLE_CLOUD_PROJECT "$google_cloud_project"
    else
        contract_error "must set FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT"
    fi

    browser_api_key="$(read_env_value ARCHBRO_FIREBASE_API_KEY)"
    require_literal_nonempty ARCHBRO_FIREBASE_API_KEY "$browser_api_key"

    # Google sign-in opens https://<authDomain>/__/auth/handler. Checking it
    # here names the missing key while the old containers are still serving;
    # left to container start it is a failed health check and a rollback.
    browser_auth_domain="$(read_env_value ARCHBRO_FIREBASE_AUTH_DOMAIN)"
    require_literal_nonempty ARCHBRO_FIREBASE_AUTH_DOMAIN "$browser_auth_domain"
}

case "$STACK" in
    archbro-main)
        # Main serves production traffic and must never fall back to local auth.
        require_production_firebase
        ;;
    archbro-dev)
        # The existing remote dev stack is an explicitly non-production staging
        # environment protected by Cloudflare Access. Keep local/local valid until
        # Firebase is actually provisioned; a future Firebase cutover must be
        # complete before deploy is allowed to recreate the app container.
        if [ "$ARCHBRO_ENV_VALUE" = "local" ] && [ "$ARCHBRO_AUTH_MODE_VALUE" = "local" ]; then
            :
        elif [ "$ARCHBRO_ENV_VALUE" = "production" ] && [ "$ARCHBRO_AUTH_MODE_VALUE" = "firebase" ]; then
            require_production_firebase
        else
            echo "::error::$ENV_FILE for archbro-dev must use local/local or a complete production/firebase configuration"
            exit 1
        fi
        ;;
    *)
        echo "::error::unsupported stack $STACK"
        exit 1
        ;;
esac

# Useful for CI/operator preflight: prove the .env contract without moving files,
# authenticating Docker, or touching containers.
if [ "${ARCHBRO_VALIDATE_ONLY:-0}" = "1" ]; then
    exit 0
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
