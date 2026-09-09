#!/usr/bin/env bash
# ============================================================================
# Server A CI/CD orchestrator for alarabia.chat (mirrors the quran-chat,
# hadith-chat and tajweed-chat orchestrators; see
# docs/ALARABIA_CHAT_OVH_MIGRATION_PLAN.md).
#
# Runs ON the server — from the systemd poll timer (dcx-deploy@alarabia-chat
# .timer) or manually. For this one app it:
#   1. fetches deploy secrets from OVH Secret Manager (OKMS) into a tmpfs env
#   2. reads the GitHub branch HEAD and skips the deploy if nothing changed
#   3. shallow-clones / fast-forwards the repo over SSH using a read-only,
#      repo-scoped GitHub deploy key
#   4. materializes the model weights from Git LFS (see below)
#   5. materializes a merged env (canonical apps.env + host.env + secrets)
#   6. docker compose build + up -d behind the shared edge Caddy
#   7. health-gates every container listed in HEALTH_CONTAINERS
#   8. seeds the edge Caddy upstream snippet and reloads Caddy
#
# This app holds no persistent state: uploads are parsed in memory and dropped,
# and results live in the process. There is no data directory to protect, which
# is the one way this script is simpler than its tajweed-chat sibling.
#
# Usage (on the server):
#   /opt/deploy/alarabia-chat/cicd/deploy.sh \
#       /opt/deploy/alarabia-chat/cicd/alarabia-chat.conf [--force]
# ============================================================================
set -euo pipefail

CONF="${1:?usage: deploy.sh <app.conf> [--force]}"
FORCE="${2:-}"

# host.env carries everything server-specific (paths, domain, resource caps),
# so this script has no host knowledge of its own and travels between servers.
HOST_ENV="${ALARABIA_HOST_ENV:-/opt/alarabia/host.env}"
# shellcheck disable=SC1090
[ -f "$HOST_ENV" ] && { set -a; source "$HOST_ENV"; set +a; }

ALARABIA_ROOT="${ALARABIA_ROOT:-/opt/alarabia}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/deploy}"
OKMS_ENV="${OKMS_ENV:-$DEPLOY_DIR/okms.env}"
APPS_ENV="${APPS_ENV:-$DEPLOY_DIR/apps.env}"
SNIPPET_DIR="${EDGE_SNIPPET_DIR:-$DEPLOY_DIR/edge/upstreams}"
EDGE_CONTAINER="${EDGE_CONTAINER:-edge-caddy}"
CICD_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNDIR="${RUNDIR:-/run/deploy}"

[ -f "$CONF" ]     || { echo "ERROR: conf $CONF not found"; exit 1; }
[ -f "$OKMS_ENV" ] || { echo "ERROR: $OKMS_ENV not found (OKMS connection)"; exit 1; }

# OKMS_* values must be exported: okms_fetch.py reads them from the
# environment. The *.conf only needs shell-local vars.
# shellcheck disable=SC1090
set -a; source "$OKMS_ENV"; set +a
# shellcheck disable=SC1090
source "$CONF"
# apps.env is the co-hosted fleet's shared domain registry. Optional, because
# a standalone host has no fleet — the domain then comes from host.env.
# shellcheck disable=SC1090
[ -f "$APPS_ENV" ] && { set -a; source "$APPS_ENV"; set +a; }

: "${APP_NAME:?conf must set APP_NAME}"
: "${OKMS_PATH:?conf must set OKMS_PATH}"
: "${REPO_SSH:?conf must set REPO_SSH}"
: "${DEPLOY_KEY:?conf must set DEPLOY_KEY}"
: "${BRANCH:?conf must set BRANCH}"
: "${SRC_DIR:?conf must set SRC_DIR}"
: "${COMPOSE_DIR:?conf must set COMPOSE_DIR}"
: "${COMPOSE_FILES:?conf must set COMPOSE_FILES}"

host_of() { echo "${1#http*://}" | sed 's#/.*$##'; }

install -d -m 700 "$RUNDIR"
SECRETS_ENV="$(mktemp "$RUNDIR/${APP_NAME}.secrets.XXXX")"
MERGED_ENV="$(mktemp "$RUNDIR/${APP_NAME}.merged.XXXX")"
chmod 600 "$SECRETS_ENV" "$MERGED_ENV"
cleanup() { shred -u "$SECRETS_ENV" "$MERGED_ENV" 2>/dev/null || rm -f "$SECRETS_ENV" "$MERGED_ENV"; }
trap cleanup EXIT

echo "==> [$APP_NAME] fetch secrets from OKMS ($OKMS_PATH)"
python3 "$CICD_DIR/okms_fetch.py" "$OKMS_PATH" "$SECRETS_ENV"

[ -r "$DEPLOY_KEY" ] || { echo "ERROR: deploy key $DEPLOY_KEY not readable"; exit 1; }
# StrictHostKeyChecking=accept-new pins github.com on first use without making
# an unattended timer deploy hang on an interactive prompt.
export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=${GIT_KNOWN_HOSTS:-$ALARABIA_ROOT/.ssh/known_hosts}"

echo "==> [$APP_NAME] check remote HEAD ($REPO_SSH@$BRANCH)"
REMOTE_SHA="$(git ls-remote "$REPO_SSH" "refs/heads/$BRANCH" | awk '{print $1}')"
[ -n "$REMOTE_SHA" ] || { echo "ERROR: cannot read remote HEAD for $REPO_SSH@$BRANCH"; exit 1; }
SHA_FILE="$SRC_DIR/.deployed_sha"
DEPLOYED_SHA="$(cat "$SHA_FILE" 2>/dev/null || true)"

# Only treat "unchanged" as up-to-date when every expected container is running.
ALL_UP=1
for c in ${HEALTH_CONTAINERS:-}; do
  docker ps --filter "name=^/${c}$" --format '{{.Names}}' | grep -q . || ALL_UP=0
done
if [ "$FORCE" != "--force" ] && [ "$REMOTE_SHA" = "$DEPLOYED_SHA" ] && [ "$ALL_UP" = 1 ]; then
  echo "==> [$APP_NAME] up to date (${REMOTE_SHA:0:12}); nothing to do."
  exit 0
fi

echo "==> [$APP_NAME] sync source -> $SRC_DIR"
install -d -m 755 "$(dirname "$SRC_DIR")"
# GIT_LFS_SKIP_SMUDGE stops the clone/checkout from pulling every LFS object it
# sees. Without it git would fetch data/*.jsonl too — 156 MB of training corpus
# that the inference image never copies and the app never reads. The weights are
# fetched explicitly, by path, in the next step.
export GIT_LFS_SKIP_SMUDGE=1
if [ -d "$SRC_DIR/.git" ]; then
  git -C "$SRC_DIR" remote set-url origin "$REPO_SSH"
  git -C "$SRC_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$SRC_DIR" checkout -f "$BRANCH" 2>/dev/null || git -C "$SRC_DIR" checkout -B "$BRANCH" FETCH_HEAD
  git -C "$SRC_DIR" reset --hard FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_SSH" "$SRC_DIR"
fi

# ── Model weights (Git LFS) ────────────────────────────────────────────────
# models/*.pt are LFS objects. A clone without this step leaves 130-byte
# pointer files, the image builds and starts perfectly, and every request then
# fails at torch.load with an unintelligible error — so verify, do not assume.
echo "==> [$APP_NAME] materialize model weights from LFS"
git -C "$SRC_DIR" lfs install --local >/dev/null
git -C "$SRC_DIR" lfs pull --include="${LFS_INCLUDE:-models/**}" --exclude=""
for w in "$SRC_DIR"/models/*.pt; do
  [ -e "$w" ] || { echo "ERROR: no model weights in $SRC_DIR/models"; exit 1; }
  # A pointer file is ~130 bytes; the smallest real checkpoint is 11 MB.
  size="$(stat -c %s "$w")"
  if [ "$size" -lt 1000000 ]; then
    echo "ERROR: $w is $size bytes — still an LFS pointer, not the weights."
    echo "       Check that git-lfs is installed and the deploy key can read"
    echo "       the repository's LFS storage."
    exit 1
  fi
done
echo "    $(ls -1 "$SRC_DIR"/models/*.pt | wc -l) checkpoints, $(du -sh "$SRC_DIR/models" | cut -f1) total"

echo "==> [$APP_NAME] materialize merged env"
# Later lines win in an --env-file, so the order is deliberate: fleet
# defaults, then this host's placement and resource caps, then the domain,
# then secrets.
{
  [ -f "$APPS_ENV" ] && cat "$APPS_ENV"
  [ -f "$HOST_ENV" ] && cat "$HOST_ENV"
  echo "ALARABIA_HOST=$(host_of "${ALARABIA_URL:-https://${ALARABIA_DOMAIN:-alarabia.chat}}")"
  cat "$SECRETS_ENV"
} > "$MERGED_ENV"

cd "$SRC_DIR/$COMPOSE_DIR"
# shellcheck disable=SC2086
COMPOSE=(docker compose $COMPOSE_FILES --env-file "$MERGED_ENV")

echo "==> [$APP_NAME] build (${REMOTE_SHA:0:12})"
"${COMPOSE[@]}" build

echo "==> [$APP_NAME] up -d"
"${COMPOSE[@]}" up -d --remove-orphans

echo "$REMOTE_SHA" > "$SHA_FILE"

# ── Health gate ────────────────────────────────────────────────────────────
overall=0
for c in ${HEALTH_CONTAINERS:-}; do
  cmd="$(health_cmd_for "$c")"
  budget="$(health_timeout_for "$c")"
  [ -n "$cmd" ] || continue
  echo "==> [$APP_NAME] health check $c (up to ${budget}s)"
  ok=""
  waited=0
  while [ "$waited" -lt "$budget" ]; do
    if docker exec "$c" sh -lc "$cmd" >/dev/null 2>&1; then ok=1; break; fi
    # Abort early if the container died rather than waiting out the budget.
    if ! docker ps --filter "name=^/${c}$" --format '{{.Names}}' | grep -q .; then
      echo "    !! $c exited; last log lines:"
      docker logs --tail 40 "$c" 2>&1 | sed 's/^/       /'
      break
    fi
    sleep 5
    waited=$((waited + 5))
  done
  if [ -n "$ok" ]; then
    echo "    $c healthy after ${waited}s"
  else
    # Soft-fail containers report but do not fail the deploy. alarabia-clamav
    # is one: a cold signature database can take longer than any sensible
    # budget, and the site serves pasted text throughout — only uploads wait.
    case " ${HEALTH_SOFT:-} " in
      *" $c "*) echo "    NOTE: $c not ready after ${waited}s (non-fatal; uploads refused until it is)" ;;
      *)        echo "    WARN: $c not healthy after ${waited}s — docker logs $c"; overall=1 ;;
    esac
  fi
done

# ── Edge routing ──────────────────────────────────────────────────────────
if declare -F render_caddy_snippets >/dev/null 2>&1 && [ -d "$SNIPPET_DIR" ]; then
  echo "==> [$APP_NAME] write edge upstream snippet"
  render_caddy_snippets
  if docker ps --filter "name=^/${EDGE_CONTAINER}$" --format '{{.Names}}' | grep -q .; then
    # Reload, never restart: the edge fronts other applications, and a reload
    # keeps their traffic flowing. If the config is bad, the old one stays live.
    if docker exec "$EDGE_CONTAINER" caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
      docker exec "$EDGE_CONTAINER" caddy reload --config /etc/caddy/Caddyfile
      echo "    $EDGE_CONTAINER reloaded"
    else
      echo "    WARN: Caddyfile did not validate; left the running config alone"
      overall=1
    fi
  fi
fi

echo "==> [$APP_NAME] deployed ${REMOTE_SHA:0:12}"
exit "$overall"
