# Working in this repository

Rules that are easy to violate by accident and expensive to undo. Full
context in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Push every commit to both repositories

This project is published twice and the copies must never diverge:

| Remote | Repository | Writable by |
| --- | --- | --- |
| `origin` | `mohammadkhair7/khair-arabic-models` | `mohammadkhair7` |
| `qurancomp` | `qurancomp/khair-arabic-models` | `qurancomp` |

A plain `git push` reaches only one of them: each repository is writable by a
different GitHub account, and git presents one credential per host. After
every commit, run:

```bash
python scripts/sync_repos.py
```

It pushes each remote with its own account active (`gh auth switch`), then
re-reads both remote heads to confirm they match, and restores the account
that was active before. Never finish a task with the two repositories at
different commits — check with `python scripts/sync_repos.py --check`.

## Never commit a credential

`SENDGRID_API_KEY` is the only secret this project uses. It is read from the
environment, never from a tracked file.

- `.env` and friends are git-ignored. `webapp/.env.example` is the one
  tracked file naming these variables, and its values are empty.
- Do not add a real value to any tracked file, including tests, fixtures,
  documentation or commit messages.
- `scripts/check_secrets.py` runs as a pre-commit hook
  (`git config core.hooksPath .githooks`) and scans for nine credential
  shapes. Run `python scripts/check_secrets.py --all` before publishing.
- Secrets must not reach an API response or a log line. `integrations.Secret`
  exists for this; keep using it.

## Git LFS

`*.pt` and `*.jsonl` are LFS objects. Without `git lfs install` you will
commit 130-byte pointers over real weights. Check `git lfs status` before
pushing.

## Tests

```bash
python -m pytest tests webapp/tests -q
```

Model-dependent tests skip when the checkpoints are missing, so a green run
with skips means the code is fine but `git lfs pull` has not been run.

## House style

- `src/arabicmodels/` may not touch a database, a network, or any
  project-specific schema. It reads files and writes files.
- Change a model, regenerate its metrics and update `docs/MODEL_CARDS.md` in
  the same commit. Numbers in the docs must come from a real `eval` run.
- Checkpoints must stay loadable under `weights_only=True`.
