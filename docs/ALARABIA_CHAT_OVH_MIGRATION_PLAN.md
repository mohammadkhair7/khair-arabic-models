# alarabia.chat — OVH deployment and migration plan

Status: **deployed and healthy on OVH Server A, waiting on DNS.** The container
is built, serving, and reachable through the shared edge Caddy by hostname; all
25 smoke-test checks pass. The one remaining step is manual: point the
`alarabia.chat` A-records at the server in Namecheap ([§6](#6-namecheap-dns-cutover-manual-step)),
after which Caddy issues the certificate and the site is public.

This document is both the record of how alarabia.chat is deployed today and the
runbook for moving it to the planned dedicated server **QC**. The organising
principle throughout is isolation: everything the app owns is in one directory,
one secret object, two containers and one Caddy snippet, so the future move is a
copy rather than an untangling.

---

## 1. What runs where

Server A is a shared OVH host (`kalimat-a`, `148.113.226.229`, Ubuntu 24.04,
12 cores / 62 GB / 878 GB). It already ran Quran.chat, Hadith.chat,
Tajweed.chat, QuranComputing.org, DeepCerebra.io/.net and Kalimat before
alarabia arrived. alarabia was onboarded as a peer of those apps rather than as
a special case, using the fleet's existing deploy framework.

| Concern | alarabia.chat |
|---|---|
| Public host | `alarabia.chat`, `www.alarabia.chat` (redirects to apex) |
| Containers | `alarabia-app`, `alarabia-clamav` |
| Image | `alarabiachat:latest`, built from `webapp/Dockerfile` |
| Source repo | `git@github.com:qurancomp/khair-arabic-models.git`, branch `main` |
| App root on host | `/opt/alarabia` |
| State directory | **none** — the app writes nothing to disk (see [§2](#2-isolation-boundaries)) |
| Secrets | OVH Secret Manager (OKMS) object `alarabia-chat/deploy` |
| CI/CD | `dcx-deploy@alarabia-chat.timer` (polls `main` every 5 min) |
| Edge | shared `edge-caddy`, snippet `/opt/deploy/edge/upstreams/alarabia.caddy` |
| Docker networks | `edge` (shared) + `alarabia-net` (private, clamd only) |

One container serves everything: the static page, `/api/process`, the download
endpoints and the feedback form. There is no `/api` split at the edge because
there is no separate frontend — `webapp/app/main.py` serves both.

The app is the free test application for this repository's four checkpoints:
structure segmentation, POS tagging, and the two diacritizers. All four are
preloaded at startup (`ARABICWEB_PRELOAD=1`), which costs about 260 MB of RSS
and makes the first public request fast.

---

## 2. Isolation boundaries

This is the section that matters for the QC move.

**alarabia.chat has no persistent state at all.** Uploads are read into memory,
parsed from a `BytesIO` and dropped; results are held in the process for
`ARABICWEB_RESULT_TTL_S` (600 s) so the download buttons work, then discarded.
There is no database, no bind mount, no docker volume owned by the app, and
nothing on disk to back up. Everything the container needs comes from the image.

That single fact is what makes the QC migration a redeploy rather than a data
transfer, and it is worth protecting: if a future feature needs to persist
something, it should be weighed against losing this property.

alarabia shares exactly three things with its neighbours, and each is additive
rather than entangling:

1. **The host kernel and Docker daemon.** Mitigated by explicit ceilings in
   `deploy/docker-compose.yml`: `mem_limit` (4 GB app, 2 GB scanner) turns a
   leak in a document parser into a single-container OOM kill, `cpus` (2.0 and
   1.0) leaves cores for the neighbours, `oom_score_adj: 500` makes the kernel
   sacrifice alarabia before it touches anyone else's database, `pids_limit`
   means a fork bomb in a parser goes nowhere, and capped `json-file` logging
   stops logs filling the shared disk.
2. **The `edge` Docker network and `edge-caddy`.** alarabia contributes two site
   blocks plus one upstream snippet it alone owns. Deploys `caddy reload` (never
   restart), and `deploy.sh` validates the config first — a bad alarabia config
   leaves the neighbours' traffic untouched.
3. **The OKMS connection credentials** in `/opt/deploy/okms.env`, used to read
   alarabia's *own* object. The object itself, `alarabia-chat/deploy`, is
   separate from `quran-chat/deploy`, `hadith-chat/deploy` and
   `tajweed-chat/deploy`.

Everything else is exclusively alarabia's:

- **The ClamAV sidecar is on a private network.** `alarabia-clamav` joins
  `alarabia-net` and *not* `edge`, so no other container on the host can reach
  it and it can never be routed to from the internet. The smoke test asserts
  both halves of that.
- **No published host port.** Traffic reaches the app only through `edge-caddy`,
  so nothing is exposed directly on `148.113.226.229`.
- **The one docker volume**, `alarabia-chat_clamav-db`, holds the ClamAV
  signature database. It belongs to the scanner, not the application: it is a
  cache, it is re-downloadable, and it is deliberately not migrated.

### 2.1 Container hardening

Inherited from `webapp/Dockerfile` and `deploy/docker-compose.yml`, and asserted
by the smoke test. This app's whole exposure is that it opens documents sent by
strangers, so the container is the outer half of a defence the application code
already implements (`webapp/app/security.py`):

| Property | Setting |
|---|---|
| User | `65534:65534` (nobody), never root |
| Filesystem | `read_only: true`, with a 64 MB `noexec,nosuid,nodev` tmpfs at `/tmp` |
| Capabilities | `cap_drop: [ALL]` — the app needs none |
| Privilege escalation | `no-new-privileges:true` |
| Process count | `pids_limit: 256` |
| Upload screening | every upload streamed to clamd before any parser opens it, `ARABICWEB_CLAMAV_REQUIRED=1` (fail closed) |

---

## 3. Repository layout

Everything needed to deploy lives in the repo, so the host holds no unique
knowledge that could be lost:

```
deploy/
├── docker-compose.yml      the app tier (containers, env, limits, networks)
├── cicd/
│   ├── deploy.sh           the orchestrator that runs on the host
│   ├── alarabia-chat.conf  app config: repo, branch, health gate, Caddy snippet
│   └── okms_fetch.py       reads one OKMS object into a 0600 env file
└── ovh/
    ├── host.env.example      template for the host-specific file
    ├── bootstrap-alarabia.sh one-shot onboarding of a new host (idempotent)
    ├── env_to_okms.ps1       loads the deploy secrets from a local .env
    ├── smoke-test.sh         post-deploy verification (25 checks)
    ├── status.sh             containers, weights, CI/CD, edge, DNS and TLS state
    └── dns_survey.sh         authoritative DNS check before/after cutover
```

There is no `deploy/Dockerfile`: the image is `webapp/Dockerfile`, built with the
repository root as context. Keeping one Dockerfile means the container you get
locally (`docker compose -f webapp/docker-compose.yml up`) is the container that
runs in production, and `webapp/README.md` stays true.

`/opt/deploy/alarabia-chat/cicd/` on the host is a byte-identical copy of
`deploy/cicd/`, matching how `quran-chat`, `hadith-chat` and `tajweed-chat` are
arranged.

### 3.1 Why not GitHub Actions

The other apps on this host settled on a pull-based timer, and alarabia follows
them rather than introducing a second pattern. A workflow that SSHes in and runs
`docker compose up` conflicts with how Server A works on three counts: it tends
to publish a port that bypasses the shared edge and its TLS, it requires
long-lived plaintext secrets on the host instead of just-in-time OKMS reads, and
it needs repo write credentials where a read-only deploy key suffices. The
pull-based timer also degrades better — if the host is briefly unreachable the
next poll simply catches up, whereas a pushed workflow run just fails.

---

## 4. Configuration inventory

Configuration is deliberately split by *what changes when the app moves*.

### 4.1 Host-specific — `/opt/alarabia/host.env`

Not secret. This is the **only** file that needs editing for the QC move; the
template is `deploy/ovh/host.env.example`.

| Key | Server A value | Note |
|---|---|---|
| `ALARABIA_ROOT` | `/opt/alarabia` | checkout, deploy key, this file |
| `ALARABIA_DOMAIN` / `ALARABIA_URL` | `alarabia.chat` / `https://alarabia.chat` | |
| `INSTITUTE_URL` / `PROJECT_URL` | `qurancomputing.org` / `hadith.chat` | footer links |
| `DONATE_MONTHLY_URL` / `DONATE_ONETIME_URL` | the two Stripe Payment Links | public URLs, see [§4.4](#44-donations-are-not-a-secret) |
| `ALARABIA_EDGE_MODE` | `CO_HOSTED` | a shared Caddy already owns :80/:443 |
| `EDGE_*` | `edge-caddy`, `/opt/deploy/edge/upstreams`, network `edge` | |
| `ALARABIA_NETWORK` | `alarabia-net` | private, clamd only |
| `ALARABIA_DEVICE` / `_PRELOAD` | `cpu` / `1` | no GPU on this host |
| `ALARABIA_MAX_UPLOAD_MB` | `8` | keep in step with the edge `request_body` |
| `ALARABIA_APP_MEM` / `_CPUS` | `4g` / `2.0` | neighbour protection |
| `ALARABIA_CLAMAV_MEM` / `_CPUS` | `2g` / `1.0` | |
| `ALARABIA_START_PERIOD` | `120s` | healthcheck grace while models preload |

Measured after the first deploy: `alarabia-app` sits at **263 MB of its 4 GB**
ceiling with all four models loaded, and `alarabia-clamav` at **952 MB of 2 GB**
(almost all of it the signature database). The ceilings have plenty of headroom.

### 4.2 Secrets — OKMS object `alarabia-chat/deploy`

Three keys, loaded by `deploy/ovh/env_to_okms.ps1`. Fetched just-in-time by
`deploy.sh` into `/run/deploy/*` (tmpfs, mode 0600) and shredded when the deploy
exits — they are never written to persistent disk.

```
SENDGRID_API_KEY   SENDGRID_FROM_EMAIL   CONTACT_EMAIL
```

`SENDGRID_API_KEY` is the only true credential this app has, and it is only used
for the feedback form. Without it the form disables itself and says so, and
everything else still works — which is why `docker-compose.yml` does **not** mark
it required. It is wrapped in `integrations.Secret`, which renders as `***` in
every repr and traceback, and `/api/config` returns a boolean saying whether
feedback is configured rather than the key. The smoke test asserts that the
config payload contains no `SG.` prefix and no `SENDGRID` string.

The other two are not secret, but they belong with it: they are account-level
settings that must stay consistent with the verified sender on the SendGrid
account, and keeping all three in one object means the QC move copies one thing.

Read the key names back (never the values) with:

```bash
sudo sh -c 'set -a; . /opt/deploy/okms.env; set +a; \
  python3 /opt/deploy/alarabia-chat/cicd/okms_fetch.py alarabia-chat/deploy /run/deploy/v.env >/dev/null; \
  cut -d= -f1 /run/deploy/v.env | paste -sd, -; shred -u /run/deploy/v.env'
```

### 4.3 Not secret, not host-specific — `deploy/docker-compose.yml`

The container's fixed internal layout: the port it listens on, the clamd
hostname on the private network, and the `ARABICWEB_*` defaults. This is why the
same OKMS object and the same `host.env` shape work unchanged on any server.

### 4.4 Donations are not a secret

The donate button offers the same two **Stripe Payment Links** tajweed.chat and
hadith.chat use, so a gift lands in the same place whichever site the visitor
came from:

| Tier | Link |
|---|---|
| Monthly donation $5 | `https://buy.stripe.com/00g3eO7Mce0v3Cg7sy` |
| One-time donation $10 | `https://buy.stripe.com/00g16GeaAbSn3CgcMV` |

Payment Links are public URLs that Stripe hosts and processes. This app never
sees a card number, so there is no Stripe secret key, no publishable key, no
webhook signature and no PCI surface — which is why the links live in
`host.env` rather than in OKMS.

The UI matches tajweed.chat: a dropdown in the masthead (`Donate ▾`) with the
two tiers, plus a full Donate page with a card per tier. Both are built from
`/api/config` by `buildDonate()` in `webapp/static/app.js`, and the labels name
the amounts because the links behind them are fixed-price. The server side is
`integrations.donation_options()`.

---

## 5. CI/CD

Pull-based, identical in shape to the other apps on the host.

```
dcx-deploy@alarabia-chat.timer   every 5 min (OnUnitActiveSec=5min)
  └─ dcx-deploy@alarabia-chat.service
       └─ /opt/deploy/alarabia-chat/cicd/deploy.sh <conf>
```

Each run: take the per-app lock → fetch secrets from OKMS → read `git ls-remote`
HEAD of `main` → exit early if the SHA is unchanged *and* both containers are
running → shallow fetch/reset the checkout → **materialize the model weights from
Git LFS** → merge `apps.env` + `host.env` + secrets into one tmpfs env file →
`docker compose build` → `up -d` → health-gate both containers → rewrite the
Caddy snippet → validate and `caddy reload`.

Source access uses a **read-only, repo-scoped GitHub deploy key**
(`/opt/alarabia/.ssh/id_ed25519_ghdeploy`, root-only, registered on the repo as
"Server A CI/CD (read-only)", key id `162812790`). No account token or write
credential exists on the host.

Push to `main` and the change is live within ~5 minutes. To deploy immediately
instead of waiting for the poll:

```bash
sudo systemctl start dcx-deploy@alarabia-chat.service     # or, to watch it:
sudo /opt/deploy/alarabia-chat/cicd/deploy.sh \
     /opt/deploy/alarabia-chat/cicd/alarabia-chat.conf --force
```

### 5.1 Git LFS is the one thing this app's deploy does differently

`models/*.pt` are Git LFS objects. This matters more than it sounds, because the
failure is silent in exactly the wrong way: without git-lfs the checkout contains
130-byte pointer files, the image **builds successfully**, the container
**starts**, the health check **passes**, and only an actual inference request
fails — inside `torch.load`, with an error that says nothing about LFS.

Three things guard against it:

1. `deploy/ovh/bootstrap-alarabia.sh` installs `git-lfs` if the host lacks it.
   Server A did (it was installed during this onboarding, `git-lfs/3.4.1`).
2. `deploy.sh` sets `GIT_LFS_SKIP_SMUDGE=1` for the clone and then pulls only
   `models/**` by path. This is not just tidiness: the repo also tracks
   `data/*.jsonl`, which is 156 MB of *training* corpus that the image never
   copies and inference never reads. Fetching by path keeps it off the server.
3. `deploy.sh` then **verifies** — any `.pt` under 1 MB is treated as a leftover
   pointer and the deploy aborts before building. The smallest real checkpoint
   is 11 MB, so the threshold is unambiguous.

The QC host needs `git-lfs` for the same reason. The bootstrap handles it.

### 5.2 What the deploy owns at the edge, and what it does not

Worth being precise about, because the boundary is easy to trip over:

- **Owned by the deploy, rewritten on every run** —
  `/opt/deploy/edge/upstreams/alarabia.caddy`, the upstream snippet, generated
  by `render_caddy_snippets()` in `alarabia-chat.conf`. Each app owns exactly one
  snippet, which is why one app's deploy can never disturb another's routing.
- **Host state, edited once, NOT tracked by this repo** — the two
  `alarabia.chat` / `www.alarabia.chat` site blocks in
  `/opt/deploy/edge/Caddyfile`. That file is the shared edge config for every app
  on the host and belongs to the fleet's ops tree, not here. It was appended in
  place by the bootstrap (the practice on this host — note the `Caddyfile.bak.*`
  trail from earlier onboardings) and a backup was taken. `bootstrap-alarabia.sh`
  is idempotent, so it also serves as the recipe if the shared file is ever
  re-rendered and the blocks need reapplying.

The site block raises `request_body max_size` to 16 MB. Caddy's default is 10 MB,
which is *below* what the application itself accepts: `ARABICWEB_MAX_UPLOAD_MB=8`
plus multipart framing pushes a full-size upload past 10 MB, and the edge would
reject it before the app could apply its own limit and explain itself. Raise both
together or neither.

### 5.3 Two bugs found while bringing this up

Both are fixed in the repository; recording them because each would otherwise be
rediscovered on QC.

**`numpy` was missing from the image.** `webapp/Dockerfile` installs the package
with `pip install --no-deps -e .`, deliberately, so that pip cannot replace the
CPU-only torch wheel with the 2 GB CUDA build from PyPI. But `--no-deps` also
drops the package's *own* dependencies, and `arabicmodels` imports `numpy` at
module scope. The container started, reported healthy, and then exited on the
preload with `ModuleNotFoundError`. `numpy` is now named in
`webapp/requirements.txt`, and the Dockerfile runs `python -c "import
arabicmodels"` at build time so a missing dependency fails the build instead of
the first model load.

**Concurrent deploys collided.** `bootstrap-alarabia.sh` ends with
`systemctl enable --now`, which fires a deploy immediately; running
`deploy.sh --force` by hand at the same moment produced two `docker compose up`
runs racing for the same container names, and the loser died on "container name
is already in use" partway through. `deploy.sh` now takes an `flock` on
`/run/deploy/alarabia-chat.lock` and *waits*, so a manual `--force` queues behind
a timer run rather than being dropped.

### 5.4 Updating `deploy.sh` itself

The host copy under `/opt/deploy/alarabia-chat/cicd/` does not update itself when
`deploy.sh` changes in the repo — `deploy.sh` is the thing running, so it cannot
replace itself mid-run. Re-run the bootstrap to pick up a change to the
orchestrator:

```bash
sudo git -C /tmp/alarabia-bootstrap fetch --depth 1 origin main
sudo git -C /tmp/alarabia-bootstrap reset --hard FETCH_HEAD
sudo /tmp/alarabia-bootstrap/deploy/ovh/bootstrap-alarabia.sh
```

The app's own code needs none of this; it is fetched fresh on every deploy.

---

## 6. Namecheap DNS cutover (manual step)

`alarabia.chat` is on Namecheap BasicDNS. **The nameservers are already correct
and must not be changed** — the domain is registered at Namecheap and delegated
to Namecheap's own DNS, which is exactly what the other `.chat` sites use:

```
dns1.registrar-servers.com
dns2.registrar-servers.com
```

Only the *records* change. Authoritative state surveyed before cutover:

| Type | Host | Current value | Action |
|---|---|---|---|
| A | `@` | `192.64.119.8` (Namecheap parking) | **CHANGE** → `148.113.226.229` |
| CNAME | `www` | `parkingpage.namecheap.com.` | **REPLACE** with an A record → `148.113.226.229` |
| MX | `@` | `eforward1-5.registrar-servers.com` (10/10/10/15/20) | **KEEP** |
| TXT | `@` | `v=spf1 include:spf.efwd.registrar-servers.com ~all` | **KEEP** |

So, precisely: in **Domain List → alarabia.chat → Advanced DNS**, the domain is
currently parked, so remove the parking records and add two of your own:

```
Type: A Record   Host: @     Value: 148.113.226.229   TTL: Automatic
Type: A Record   Host: www   Value: 148.113.226.229   TTL: Automatic
```

Delete the `URL Redirect` / parking entries for `@` and `www` if Namecheap's
editor shows them. Leave the five MX records and the SPF TXT record untouched —
they carry Namecheap's email forwarding for the domain, and removing them would
break mail.

Notes:

- The apex must be an **A record**, not a CNAME. Server A is addressed by IP,
  exactly as `quran.chat`, `hadith.chat` and `tajweed.chat` already are.
- `www` must become an **A record too**, not a CNAME to the apex. The edge gives
  `www` its own site block that permanently redirects to the apex, so it needs to
  resolve to the server in its own right.
- No `AAAA` record. The host has IPv6 (`2607:5300:223:e500::`) but the rest of
  the fleet is IPv4-only at the edge; adding AAAA without verifying the edge
  listens on it would black-hole IPv6 clients.
- No `CAA` record exists, so Let's Encrypt is unrestricted. Nothing to add.
- TLS is automatic once DNS resolves here — but see
  [§6.2](#62-you-will-need-a-forced-reload-after-the-cutover), which applies to
  this cutover specifically.

### 6.1 Verification after the cutover

```bash
# From the server: authoritative records now point here
sudo /tmp/alarabia-bootstrap/deploy/ovh/dns_survey.sh alarabia.chat

# From anywhere: certificate issued and the app serves
curl -sSI https://alarabia.chat/ | head -3
curl -sS  https://alarabia.chat/api/health
curl -sSI https://www.alarabia.chat/ | head -3     # expect 301 to the apex

# The models actually running behind TLS
curl -sS https://alarabia.chat/api/process \
     -d 'task=tashkeel' -d 'text=قال رسول الله'

# Everything at once
sudo /tmp/alarabia-bootstrap/deploy/ovh/smoke-test.sh
sudo /tmp/alarabia-bootstrap/deploy/ovh/status.sh
```

Certificate issuance can be followed with
`sudo docker logs -f edge-caddy 2>&1 | grep -i alarabia`.

### 6.2 You will need a forced reload after the cutover

This is not hypothetical here — it has **already happened** on this deployment,
and the logs show it. Caddy attempts issuance **when the config is loaded**, not
lazily on the first HTTPS request. The site blocks were added at bootstrap, while
DNS still pointed at Namecheap's parking IP, so Caddy spent its attempt
immediately and Let's Encrypt tried to fetch the HTTP-01 challenge from the
parking host, which does not answer on port 80:

```
validating authorization  identifier=alarabia.chat
  problem={"type":"...:error:connection",
           "detail":"192.64.119.8: Timeout during connect (likely firewall problem)"}
```

Caddy then fell back to the secondary ZeroSSL issuer and entered exponential
backoff. While in that state the browser reports `ERR_SSL_PROTOCOL_ERROR` — not a
protocol mismatch at all, just a TLS handshake aborting because no certificate
exists for the requested name.

Fixing DNS does **not** by itself end the backoff, and a plain `caddy reload` is
a no-op because the adapted config is byte-identical — the config was always
correct; only DNS was wrong at the moment of the attempt. After the A records are
live, force it:

```bash
sudo docker exec -w /etc/caddy edge-caddy \
     caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --force
```

`--force` re-provisions the `tls` app, which re-runs certificate management for
any subject lacking a certificate. This is safe for the neighbours: their
certificates already exist in storage and are simply reloaded, so nothing is
re-issued and no ACME quota is spent on them, and the reload is graceful, so no
listener drops and in-flight requests finish.

Related trap seen on the tajweed cutover and worth expecting here:
**negative DNS caching.** A workstation can keep returning NXDOMAIN for `www`
after the record exists, because the earlier NXDOMAIN was cached locally. The
server sees it correctly and Let's Encrypt validates it. `ipconfig /flushdns` (or
waiting out the negative TTL) clears it.

---

## 7. Operations runbook

```bash
# Status and logs
sudo /tmp/alarabia-bootstrap/deploy/ovh/status.sh
sudo docker ps --filter name=alarabia
sudo docker logs --tail 100 -f alarabia-app
sudo journalctl -u dcx-deploy@alarabia-chat.service -n 50

# Deploy now / force a rebuild
sudo systemctl start dcx-deploy@alarabia-chat.service
sudo /opt/deploy/alarabia-chat/cicd/deploy.sh \
     /opt/deploy/alarabia-chat/cicd/alarabia-chat.conf --force

# Verify a deploy (app, models, security posture, edge, neighbours)
sudo /tmp/alarabia-bootstrap/deploy/ovh/smoke-test.sh

# Pause automatic deploys (e.g. during maintenance)
sudo systemctl disable --now dcx-deploy@alarabia-chat.timer

# Rotate the SendGrid key
pwsh -File deploy/ovh/env_to_okms.ps1     # after editing webapp/.env
sudo systemctl start dcx-deploy@alarabia-chat.service
```

**Rollback.** Revert the commit on `main` and let the timer redeploy, or pin the
checkout by hand:

```bash
sudo git -C /opt/alarabia/src fetch --depth 50 origin main
sudo git -C /opt/alarabia/src reset --hard <good-sha>
sudo /opt/deploy/alarabia-chat/cicd/deploy.sh \
     /opt/deploy/alarabia-chat/cicd/alarabia-chat.conf --force
```

**Backup.** There is nothing to back up. The app has no state; the image is
rebuilt from the repo; the secrets are in OKMS; the ClamAV signature database is
a re-downloadable cache. The only irreplaceable things are the repository itself
(published to two GitHub remotes, see `AGENTS.md`) and the OKMS object.

**If the feedback form stops working**, check the key rather than the app:
`/api/config` reports `"feedback": false` when `SENDGRID_API_KEY` is absent or
empty, which is what an expired or rotated key looks like from outside.

**If uploads start failing but pasted text works**, clamd is down.
`ARABICWEB_CLAMAV_REQUIRED=1` means uploads fail closed by design. Check
`docker logs alarabia-clamav`; the usual cause is a signature-database update.

---

## 8. Migrating to Server QC

The work done above reduces this to a bootstrap, a secret copy and a DNS change.
There is **no data to transfer**. Nothing in `deploy/` or in the OKMS object
needs to change; only `host.env` does, and only if paths or ceilings differ.

**Prerequisites on QC.** Docker with the Compose plugin; `git` and `git-lfs`;
the fleet deploy framework at `/opt/deploy` (`okms.env` with the mTLS client
cert, `apps.env`, `cicd/okms_put.py`, `cicd/okms_fetch.py`); a shared
`edge-caddy` and an external `edge` network, or a Caddy of its own (see
[§8.1](#81-if-qc-is-a-dedicated-single-app-host)); and the `dcx-deploy@.service`
/ `.timer` systemd templates.

1. **Give QC read access to the repo.** Generate a fresh key and register it as
   a read-only deploy key, rather than copying Server A's:

   ```bash
   sudo install -d -m 700 /opt/alarabia/.ssh
   sudo ssh-keygen -t ed25519 -N '' -C 'alarabia-chat-QC-deploy-ro' \
        -f /opt/alarabia/.ssh/id_ed25519_ghdeploy
   sudo ssh-keyscan github.com | sudo tee /opt/alarabia/.ssh/known_hosts >/dev/null
   sudo cat /opt/alarabia/.ssh/id_ed25519_ghdeploy.pub
   # then, as the qurancomp account:
   gh auth switch --user qurancomp
   gh repo deploy-key add <that.pub> --repo qurancomp/khair-arabic-models \
      --title 'Server QC CI/CD (read-only)'
   ```

   Confirm it can reach the **LFS** objects, not just the refs — a deploy key
   that clones fine can still fail to fetch LFS if the repo's storage is
   restricted:

   ```bash
   sudo GIT_SSH_COMMAND='ssh -i /opt/alarabia/.ssh/id_ed25519_ghdeploy -o IdentitiesOnly=yes' \
        GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \
        git@github.com:qurancomp/khair-arabic-models.git /tmp/alarabia-bootstrap
   cd /tmp/alarabia-bootstrap && sudo git lfs pull --include='models/**' && ls -lh models/
   ```

2. **Onboard the host.** Run the same idempotent script used on Server A:

   ```bash
   sudo ALARABIA_ROOT=/opt/alarabia \
        /tmp/alarabia-bootstrap/deploy/ovh/bootstrap-alarabia.sh
   ```

   This creates `/opt/alarabia{,/.ssh}`, installs `git-lfs` if missing, installs
   `host.env` from the template, installs the CI/CD stack into
   `/opt/deploy/alarabia-chat/cicd`, registers `ALARABIA_URL` in `apps.env`, adds
   the two site blocks and the upstream snippet to the edge Caddy, and enables
   the timer. Then edit `/opt/alarabia/host.env` if QC uses different paths or
   ceilings.

3. **Copy the secrets.** The object is server-independent, so read it on A and
   write it on QC. Both hosts must be enrolled in the same OKMS.

   ```bash
   # on Server A
   sudo sh -c 'set -a; . /opt/deploy/okms.env; set +a; \
     python3 /opt/deploy/cicd/okms_fetch.py alarabia-chat/deploy /run/deploy/ar.env'
   # transfer securely, then on QC
   sudo sh -c 'set -a; . /opt/deploy/okms.env; set +a; \
     python3 /opt/deploy/cicd/okms_put.py alarabia-chat/deploy /run/deploy/ar.env'
   sudo shred -u /run/deploy/ar.env    # on BOTH hosts
   ```

   If QC belongs to a different OKMS instance, simply re-run
   `deploy/ovh/env_to_okms.ps1 -Server qc` from a workstation holding
   `webapp/.env`.

4. **There is no step for state.** This is the payoff of [§2](#2-isolation-boundaries):
   no database dump, no `rsync`, no volume export, no consistency window, and no
   need to stop the app on Server A at any point.

5. **Deploy on QC and verify before touching DNS.**

   ```bash
   sudo /opt/deploy/alarabia-chat/cicd/deploy.sh \
        /opt/deploy/alarabia-chat/cicd/alarabia-chat.conf --force
   sudo /tmp/alarabia-bootstrap/deploy/ovh/smoke-test.sh
   ```

   §5 of the smoke test proves hostname routing with a `Host:` header before DNS
   moves, so the only new variable at cutover is DNS plus certificate issuance.

6. **Cut DNS over.** Change the two A records from [§6](#6-namecheap-dns-cutover-manual-step)
   to QC's IP. Keep the MX and TXT records untouched. QC's Caddy then needs a
   certificate of its own — and it attempts issuance when its config loads, not
   on first request. Since QC's site blocks were added in step 2, before DNS
   moved, that attempt already failed against Server A and Caddy is in backoff,
   so force it exactly as in [§6.2](#62-you-will-need-a-forced-reload-after-the-cutover):

   ```bash
   # on QC
   sudo docker exec -w /etc/caddy edge-caddy \
        caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --force
   ```

   Because step 7 has not run yet, Server A is still deployed and still holds a
   valid certificate throughout the overlap. Rollback is therefore just pointing
   the two A records back at Server A's IP — no rebuild, no re-issuance.

7. **Retire alarabia on Server A** once QC is confirmed: disable the timer,
   remove the containers, image and the clamav volume, delete the site blocks and
   snippet from the edge Caddy and reload it, revoke Server A's deploy key on
   GitHub, and only then delete `/opt/alarabia`.

   ```bash
   sudo systemctl disable --now dcx-deploy@alarabia-chat.timer
   sudo docker rm -f alarabia-app alarabia-clamav
   sudo docker rmi alarabiachat:latest
   sudo docker volume rm alarabia-chat_clamav-db
   sudo docker network rm alarabia-net
   sudo rm /opt/deploy/edge/upstreams/alarabia.caddy
   # remove the two alarabia.chat site blocks from /opt/deploy/edge/Caddyfile, then:
   sudo docker exec edge-caddy caddy validate --config /etc/caddy/Caddyfile \
     && sudo docker exec edge-caddy caddy reload --config /etc/caddy/Caddyfile
   # remove ALARABIA_URL from /opt/deploy/apps.env
   sudo rm -rf /opt/alarabia /opt/deploy/alarabia-chat
   gh repo deploy-key delete 162812790 --repo qurancomp/khair-arabic-models
   ```

### 8.1 If QC is a dedicated single-app host

Set `ALARABIA_EDGE_MODE=STANDALONE` in `host.env` and give the app its own Caddy
owning :80/:443. The Caddyfile it needs is small — the two site blocks from
[§5.2](#52-what-the-deploy-owns-at-the-edge-and-what-it-does-not) with the
`import` replaced by the contents of `alarabia.caddy`:

```
{
	email admin@qurancomputing.org
}

alarabia.chat {
	encode zstd gzip
	request_body {
		max_size 16MB
	}
	reverse_proxy alarabia-app:8000 {
		flush_interval -1
	}
}

www.alarabia.chat {
	redir https://alarabia.chat{uri} permanent
}
```

The resource ceilings can then be relaxed, since there are no neighbours to
protect: raise `ALARABIA_APP_MEM` / `_CPUS`, and drop `oom_score_adj` from
`deploy/docker-compose.yml`.

---

## 9. Open items

- **`www` has never resolved.** The site block is in place and the smoke test
  confirms the hostname matches it, but until the `www` A record exists the
  redirect cannot be exercised end to end. Verify it after the cutover.
- **Sender domain.** `SENDGRID_FROM_EMAIL` and `CONTACT_EMAIL` are
  `info@hadith.chat`, inherited from the sibling project, so feedback from
  alarabia.chat arrives in the hadith.chat inbox and is sent from a hadith.chat
  address. That is deliberate for now — `SITE_DOMAIN` puts `alarabia.chat` in the
  subject line so the mail is attributable. Moving to an `@alarabia.chat` sender
  needs a verified sender on the SendGrid account first, and the domain currently
  has only Namecheap email forwarding.
- **IPv6.** The host has an IPv6 address but the edge is IPv4-only in practice.
  Adding AAAA records fleet-wide would need the edge verified on IPv6 first.
- **ClamAV memory.** The scanner sits at 952 MB of its 2 GB ceiling with the
  signature database loaded — a healthy margin today, but the database grows
  monotonically. Worth watching, and cheap to raise via
  `ALARABIA_CLAMAV_MEM` in `host.env`.
