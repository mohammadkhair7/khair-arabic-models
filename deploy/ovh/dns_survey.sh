#!/usr/bin/env bash
# Survey the live DNS for a domain before/after repointing it at this server.
#
# The delegation is read from the TLD registry rather than from a recursive
# resolver: when the apex carries a CNAME/ALIAS (as a parked Namecheap domain
# does) a recursive `dig NS` can follow that CNAME and report the parking
# provider instead of the real nameservers. Records are then read straight from
# the authoritative server, so the answers reflect what the registrar's zone
# editor actually holds rather than a stale cache.
#
# Used while bringing alarabia.chat up on Server A; see
# docs/ALARABIA_CHAT_OVH_MIGRATION_PLAN.md.
#
#   ./dns_survey.sh alarabia.chat
set -euo pipefail
DOMAIN="${1:?usage: dns_survey.sh <domain>}"
TLD="${DOMAIN##*.}"

echo "### delegation (from the .$TLD registry)"
TLD_NS="$(dig +short NS "$TLD." @8.8.8.8 | sort | head -1)"
[ -n "$TLD_NS" ] || { echo "cannot resolve nameservers for .$TLD"; exit 1; }
mapfile -t NSLIST < <(dig NS "$DOMAIN" "@$TLD_NS" +norecurse +noall +authority +additional 2>/dev/null \
                      | awk '$4=="NS" {print $5}' | sort -u)
if [ "${#NSLIST[@]}" -eq 0 ]; then
  mapfile -t NSLIST < <(dig +short NS "$DOMAIN" @8.8.8.8 | sort -u)
fi
printf '    %s\n' "${NSLIST[@]}"

AUTH="${NSLIST[0]:-}"
[ -n "$AUTH" ] || { echo "cannot find authoritative NS"; exit 1; }
echo
echo "### records, straight from $AUTH"
for name in "$DOMAIN" "www.$DOMAIN"; do
  for t in A AAAA CNAME MX TXT CAA; do
    out="$(dig "$t" "$name" "@$AUTH" +norecurse +noall +answer 2>/dev/null \
           | awk '{ $1=$1; print }' | paste -sd' ;; ' -)"
    [ -n "$out" ] && printf '  %-6s %-20s %s\n' "$t" "$name" "$out"
  done
done
