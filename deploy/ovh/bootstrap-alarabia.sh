#!/usr/bin/env bash
# ============================================================================
# One-shot onboarding of alarabia.chat onto an OVH host that ALREADY runs the
# shared Kalimat-fleet deploy framework (/opt/deploy with okms.env, apps.env
# and the edge Caddy). Idempotent: safe to re-run.
#
# What it does, in order:
#   1. creates the isolated app root (no state directory — the app writes none)
#   2. ensures git-lfs is present, because the model weights are LFS objects
#   3. installs host.env (host-specific paths, domain, resource caps)
#   4. installs the per-app CI/CD stack into /opt/deploy/alarabia-chat/cicd
#   5. registers the domain in the fleet's apps.env registry
#   6. adds the alarabia.chat site blocks to the edge Caddyfile + upstream snippet
#   7. enables the systemd poll timer (dcx-deploy@alarabia-chat)
#
# It does NOT write secrets (see okms_onboard_secrets.sh) and does NOT run a
# deploy — that is `systemctl start dcx-deploy@alarabia-chat`.
#
# Run from the directory holding this script's siblings (host.env.example and
# ../cicd/*), as root:
#   sudo ./bootstrap-alarabia.sh
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CICD_SRC="${CICD_SRC:-$HERE/../cicd}"

ALARABIA_ROOT="${ALARABIA_ROOT:-/opt/alarabia}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/deploy}"
APP_DEPLOY_DIR="$DEPLOY_DIR/alarabia-chat/cicd"
APPS_ENV="$DEPLOY_DIR/apps.env"
EDGE_DIR="$DEPLOY_DIR/edge"
CADDYFILE="$EDGE_DIR/Caddyfile"
SNIPPET_DIR="$EDGE_DIR/upstreams"
DOMAIN="${ALARABIA_DOMAIN:-alarabia.chat}"

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root (sudo)"; exit 1; }

echo "==> 1/7 app root"
# No data directory on purpose: uploads are parsed in memory and dropped, and
# results live in the process. Nothing this app owns survives a restart, which
# is what makes the QC migration a redeploy rather than a data transfer.
install -d -m 755 "$ALARABIA_ROOT"
install -d -m 700 "$ALARABIA_ROOT/.ssh"

echo "==> 2/7 git-lfs"
# models/*.pt are LFS objects. Without git-lfs the checkout silently contains
# 130-byte pointer files, the image builds, the container starts, and every
# request then dies inside torch.load. Fail here instead, where it is obvious.
if command -v git-lfs >/dev/null 2>&1; then
  echo "    present: $(git lfs version)"
else
  echo "    installing git-lfs"
  apt-get update -qq && apt-get install -y -qq git-lfs
  git lfs install --system --skip-repo
  echo "    installed: $(git lfs version)"
fi

echo "==> 3/7 host.env"
if [ -f "$ALARABIA_ROOT/host.env" ]; then
  echo "    exists, left alone: $ALARABIA_ROOT/host.env"
else
  install -m 644 "$HERE/host.env.example" "$ALARABIA_ROOT/host.env"
  echo "    installed from host.env.example"
fi

echo "==> 4/7 per-app CI/CD stack"
install -d -m 755 "$APP_DEPLOY_DIR"
install -m 755 "$CICD_SRC/deploy.sh"            "$APP_DEPLOY_DIR/deploy.sh"
install -m 644 "$CICD_SRC/alarabia-chat.conf"   "$APP_DEPLOY_DIR/alarabia-chat.conf"
install -m 644 "$CICD_SRC/okms_fetch.py"        "$APP_DEPLOY_DIR/okms_fetch.py"
# CRLF here would make the shebang unusable and silently break the timer.
sed -i 's/\r$//' "$APP_DEPLOY_DIR/deploy.sh" "$APP_DEPLOY_DIR/alarabia-chat.conf" \
                 "$APP_DEPLOY_DIR/okms_fetch.py" "$ALARABIA_ROOT/host.env"
bash -n "$APP_DEPLOY_DIR/deploy.sh"
bash -n "$APP_DEPLOY_DIR/alarabia-chat.conf"
echo "    installed + syntax-checked"

echo "==> 5/7 register the domain in the fleet registry"
if grep -q '^ALARABIA_URL=' "$APPS_ENV" 2>/dev/null; then
  echo "    ALARABIA_URL already in apps.env"
else
  cat >> "$APPS_ENV" <<EOF

# ── alarabia.chat (github.com/qurancomp/khair-arabic-models) ──
# The free test application for the khair-arabic-models checkpoints:
# diacritization, POS tagging and hadith-structure segmentation over pasted
# text or an uploaded document. Fully separate stack (alarabia-app +
# alarabia-clamav containers, private alarabia-net, OKMS object
# alarabia-chat/deploy). Stateless — no database, no volume, no bind mount.
ALARABIA_URL=https://$DOMAIN
EOF
  echo "    appended ALARABIA_URL=https://$DOMAIN"
fi

echo "==> 6/7 edge routing"
install -d -m 755 "$SNIPPET_DIR"
if [ ! -f "$SNIPPET_DIR/alarabia.caddy" ]; then
  printf 'reverse_proxy alarabia-app:8000 {\n\tflush_interval -1\n}\n' > "$SNIPPET_DIR/alarabia.caddy"
  echo "    seeded upstreams/alarabia.caddy"
else
  echo "    upstreams/alarabia.caddy exists"
fi
if grep -qE "^${DOMAIN//./\\.} \{" "$CADDYFILE" 2>/dev/null; then
  echo "    site block already in Caddyfile"
else
  cp -a "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
  cat >> "$CADDYFILE" <<EOF

# -- alarabia.chat -- the models' free test application ----------------------
# One Python container serves the whole app: static page, /api/process and the
# download endpoints. No /api split, and no state anywhere behind it.
#
# request_body is raised above Caddy's 10 MB default because the application
# accepts 8 MB uploads and multipart framing pushes a full-size one past 10 MB;
# left at the default the edge would reject it before the app could apply its
# own limit and explain itself. Keep this in step with ALARABIA_MAX_UPLOAD_MB.
$DOMAIN {
	encode zstd gzip
	request_body {
		max_size 16MB
	}
	import /etc/caddy/upstreams/alarabia.caddy
}

# -- alarabia.chat -- www -> apex canonical redirect -------------------------
# Deliberately a SEPARATE site block, not a second hostname on the apex block:
# Caddy issues one certificate per block, so pairing them would make the apex
# certificate wait on a www HTTP-01 challenge, and the apex would come up with
# no working certificate at all if www's DNS lagged.
www.$DOMAIN {
	redir https://$DOMAIN{uri} permanent
}
EOF
  echo "    appended $DOMAIN site blocks (backup taken)"
fi

echo "==> 7/7 systemd poll timer"
systemctl enable --now dcx-deploy@alarabia-chat.timer
systemctl list-timers 'dcx-deploy@alarabia-chat*' --all --no-pager || true

echo
echo "Bootstrap complete. Remaining steps:"
echo "  1. register a read-only deploy key on the repo:"
echo "       ssh-keygen -t ed25519 -N '' -C 'alarabia-chat-deploy-ro' \\"
echo "         -f $ALARABIA_ROOT/.ssh/id_ed25519_ghdeploy"
echo "       ssh-keyscan github.com > $ALARABIA_ROOT/.ssh/known_hosts"
echo "  2. write the deploy secrets:  $DEPLOY_DIR/cicd/okms_put.py alarabia-chat/deploy <env>"
echo "  3. first deploy:              systemctl start dcx-deploy@alarabia-chat.service"
echo "  4. point DNS at this host, then Caddy issues the certificate."
