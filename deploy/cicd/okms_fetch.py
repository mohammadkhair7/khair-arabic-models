#!/usr/bin/env python3
"""On-node OKMS fetcher for a SINGLE secret object → a KEY=VALUE env file (0600).

Used by the Server A CI/CD orchestrator (cicd/deploy.sh) to materialize an app's
deploy secrets from OVH Secret Manager just-in-time. Stdlib only (urllib+ssl);
never prints secret values.

  OKMS_* connection vars come from the environment (source /opt/deploy/okms.env).

Usage:
  python3 okms_fetch.py <okms/path> <out.env>
"""
import json
import os
import ssl
import sys
import urllib.request


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: okms_fetch.py <okms/path> <out.env>", file=sys.stderr)
        return 2
    path, out = sys.argv[1], sys.argv[2]
    endpoint = os.environ["OKMS_ENDPOINT"].rstrip("/")
    okms_id = os.environ["OKMS_ID"]
    ca = os.environ.get("OKMS_CA")
    ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    ctx.load_cert_chain(os.environ["OKMS_CLIENT_CERT"], os.environ["OKMS_CLIENT_KEY"])

    url = f"{endpoint}/api/{okms_id}/v1/secret/data/{path.lstrip('/')}"
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), context=ctx, timeout=30) as r:
        data = json.loads(r.read().decode()).get("data", {}).get("data", {})

    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for k, v in sorted(data.items()):
            if v is not None:
                f.write(f"{k}={v}\n")
    print(f"[okms_fetch] wrote {len(data)} keys to {out} (values withheld)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
