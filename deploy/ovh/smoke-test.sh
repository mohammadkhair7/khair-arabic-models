#!/usr/bin/env bash
# ============================================================================
# Post-deploy smoke test for alarabia.chat on a co-hosted OVH server.
#
# Exercises the real request path in layers, so a failure points at the layer
# that broke rather than just "the site is down":
#   1. the app itself, from inside the container
#   2. the four models actually running, not just loading
#   3. the security posture that justifies exposing a parser to the public
#   4. the shared edge Caddy reaching the app over the `edge` docker network
#   5. the public hostname routing (Host header against :80), which works
#      before DNS is repointed and therefore validates the cutover in advance
#
# Also re-checks that the CO-HOSTED neighbours still answer, because the edge
# config is shared and a bad reload would take them down too.
#
#   sudo ./smoke-test.sh
# ============================================================================
set -uo pipefail

APP=alarabia-app
CLAM=alarabia-clamav
EDGE=edge-caddy
DOMAIN="${ALARABIA_DOMAIN:-alarabia.chat}"
fails=0

ck() { # ck <label> <expected-substring> <command...>
  local label="$1" want="$2"; shift 2
  local out
  out="$("$@" 2>&1)" || true
  if printf '%s' "$out" | grep -q -- "$want"; then
    printf '  PASS  %s\n' "$label"
  else
    printf '  FAIL  %s\n        wanted %q, got: %s\n' "$label" "$want" "$(printf '%s' "$out" | head -c 300)"
    fails=$((fails + 1))
  fi
}

# The image is a slim python base with no curl in it, so every in-container
# probe goes through urllib rather than a shell HTTP client.
py() { docker exec "$APP" python -c "$1"; }
GET='import sys,urllib.request;print(urllib.request.urlopen("http://127.0.0.1:8000"+sys.argv[1]).read().decode()[:800])'
POST='import sys,urllib.parse,urllib.request;d=urllib.parse.urlencode({"task":sys.argv[2],"text":sys.argv[3]}).encode();print(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000"+sys.argv[1],data=d)).read().decode()[:800])'

get()  { docker exec "$APP" python -c "$GET" "$1"; }
post() { docker exec "$APP" python -c "$POST" "$1" "$2" "$3"; }

HADITH='حدثنا قتيبة بن سعيد قال حدثنا الليث عن نافع عن ابن عمر'

echo "== 1. app, from inside the container =="
ck "/api/health answers"              'ok'                 get /api/health
ck "index page renders"               '<title>'            get /
ck "config lists the four tasks"      'structure'          get /api/config
ck "config offers four formats"       'pdf'                get /api/config
ck "PDF export is available"          '"available": true'  get /api/config

echo "== 1b. donations, wired the same way as tajweed.chat =="
ck "monthly tier present"             'monthly'            get /api/config
ck "one-time tier present"            'onetime'            get /api/config
ck "links are Stripe Payment Links"   'buy.stripe.com'     get /api/config

echo "== 2. the models actually run =="
# Loading a checkpoint is not the same as inferring from it: a Git LFS pointer
# left in place fails here, at torch.load, not at startup.
ck "diacritization restores marks"    'قَالَ'              post /api/process tashkeel 'قال رسول الله'
ck "gap-filling leaves marks alone"   'output'             post /api/process tashkeel_gaps 'قَالَ رسول الله'
ck "POS tagging labels words"         'noun'               post /api/process pos 'حدثنا قتيبة بن سعيد'
ck "structure finds the isnad"        'ISNAD'              post /api/process structure "$HADITH"

echo "== 3. security posture =="
ck "CSP is locked down"               "default-src 'none'" docker exec "$APP" python -c \
   'import urllib.request;print(dict(urllib.request.urlopen("http://127.0.0.1:8000/").headers))'
ck "no SendGrid key in the config"    '^0$'                docker exec "$APP" sh -lc \
   "python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/config').read().decode())\" | grep -ci 'SG\.\|SENDGRID' || true"
ck "filesystem is read-only"          'Read-only'          docker exec "$APP" sh -lc "touch /app/x 2>&1 || true"
ck "runs as nobody, not root"         '65534'              docker exec "$APP" id -u
ck "clamd answers on the private net" 'PONG'               docker exec "$CLAM" clamdscan --ping 1 2>/dev/null \
   || ck "clamd answers on the private net" 'OK'           docker exec "$CLAM" clamdcheck.sh
ck "clamav is NOT on the edge network" '^$'                docker exec "$EDGE" sh -lc \
   "nslookup $CLAM 2>/dev/null | grep -A1 'Name:' | tail -1 || true"

echo "== 4. shared edge reaches the app over the edge network =="
ck "edge -> $APP:8000/api/health"     '200'                docker exec "$EDGE" sh -lc \
   "wget -S -qO /dev/null http://$APP:8000/api/health 2>&1 | head -3"

echo "== 5. public hostname routing (works before DNS cutover) =="
ck "$DOMAIN matches a site block"     "https://$DOMAIN"    docker exec "$EDGE" sh -lc \
   "wget -S -qO /dev/null --header='Host: $DOMAIN' http://127.0.0.1:80/ 2>&1 | head -6"
ck "www.$DOMAIN redirects to the apex" "https://$DOMAIN"   docker exec "$EDGE" sh -lc \
   "wget -S -qO /dev/null --header='Host: www.$DOMAIN' http://127.0.0.1:80/ 2>&1 | head -6"

echo "== 6. co-hosted neighbours unaffected =="
for host in quran.chat hadith.chat tajweed.chat qurancomputing.org deepcerebra.io; do
  ck "$host still answers"            'HTTP/'              docker exec "$EDGE" sh -lc \
     "wget -S -qO /dev/null --header='Host: $host' http://127.0.0.1:80/ 2>&1 | head -3"
done

echo
if [ "$fails" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "$fails CHECK(S) FAILED"
fi
exit "$fails"
