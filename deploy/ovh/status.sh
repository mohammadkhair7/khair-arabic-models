#!/usr/bin/env bash
# ============================================================================
# One-screen state of alarabia.chat on the host it is deployed to: containers,
# resource use, model weights, CI/CD timer, edge routing, DNS and TLS.
#
# Read-only — safe to run at any time.
#
#   sudo ./status.sh
# ============================================================================
set -uo pipefail

APP=alarabia-app
CLAM=alarabia-clamav
EDGE=edge-caddy
ROOT="${ALARABIA_ROOT:-/opt/alarabia}"
DOMAIN="${ALARABIA_DOMAIN:-alarabia.chat}"

hr() { printf '\n── %s %s\n' "$1" "$(printf '─%.0s' $(seq 1 $((66 - ${#1}))))"; }

hr "containers"
docker ps -a --filter "name=^/$APP$" --filter "name=^/$CLAM$" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

hr "resource use against the ceilings in host.env"
docker stats --no-stream --format \
  'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' "$APP" "$CLAM" 2>/dev/null

hr "model weights in the checkout (LFS objects, not pointers)"
# A pointer file is ~130 bytes. Anything in that range here means git-lfs did
# not run and the app will fail at torch.load on the first request.
if [ -d "$ROOT/src/models" ]; then
  ls -lh "$ROOT/src/models"/*.pt 2>/dev/null | awk '{printf "  %-34s %s\n", $9, $5}'
  small="$(find "$ROOT/src/models" -name '*.pt' -size -1M | wc -l)"
  [ "$small" -eq 0 ] && echo "  all checkpoints are real weights" \
                     || echo "  !! $small file(s) are still LFS pointers"
else
  echo "  no checkout at $ROOT/src"
fi

hr "deployed commit"
sha="$(cat "$ROOT/src/.deployed_sha" 2>/dev/null || echo '(none)')"
echo "  $sha"
git -C "$ROOT/src" log -1 --format='  %h %ad %s' --date=short 2>/dev/null || true

hr "CI/CD timer"
systemctl list-timers 'dcx-deploy@alarabia-chat*' --all --no-pager 2>/dev/null | head -3
echo "  last run:"
journalctl -u dcx-deploy@alarabia-chat.service -n 6 --no-pager -o cat 2>/dev/null | sed 's/^/    /'

hr "edge routing"
echo "  upstream snippet:"
sed 's/^/    /' /opt/deploy/edge/upstreams/alarabia.caddy 2>/dev/null || echo "    (missing)"
echo "  site blocks in the shared Caddyfile:"
grep -nE "^(www\.)?${DOMAIN//./\\.} \{" /opt/deploy/edge/Caddyfile 2>/dev/null | sed 's/^/    /' \
  || echo "    (none)"
echo "  edge -> app:"
docker exec "$EDGE" sh -lc "wget -S -qO /dev/null http://$APP:8000/api/health 2>&1 | head -1" \
  2>/dev/null | sed 's/^/    /'

hr "DNS"
for n in "$DOMAIN" "www.$DOMAIN"; do
  printf '  %-22s %s\n' "$n" "$(dig +short A "$n" | paste -sd' ' - || echo '(none)')"
done
echo "  this host: $(hostname -I | awk '{print $1}')"

hr "TLS"
for n in "$DOMAIN" "www.$DOMAIN"; do
  exp="$(echo | timeout 8 openssl s_client -servername "$n" -connect "$n":443 2>/dev/null \
        | openssl x509 -noout -issuer -enddate 2>/dev/null | paste -sd' ' -)"
  printf '  %-22s %s\n' "$n" "${exp:-(no certificate yet)}"
done
echo
echo "  recent ACME activity for this domain:"
docker logs --tail 400 "$EDGE" 2>&1 | grep -i "$DOMAIN" | tail -5 | sed 's/^/    /' \
  || echo "    (none)"
echo
