# Contributing

## Setup

```bash
git clone <repo> && cd khair-arabic-models
git lfs install && git lfs pull      # weights and datasets
pip install -e ".[dev]"
pytest
```

Model-dependent tests skip automatically if `git lfs pull` has not run, so a
green run with skips means the code is fine but the weights are missing.

## Working with LFS

`*.pt` and `*.jsonl` are Git LFS objects. Without `git lfs install` you will
commit 130-byte pointer files over real weights, which is easy to do and
annoying to undo. Verify before pushing:

```bash
git lfs status
git lfs ls-files          # should list 4 checkpoints + 4 datasets
```

## Publishing: two repositories, always in step

This project is published twice, and the two copies must never diverge:

| Remote | Repository | Account that can write to it |
| --- | --- | --- |
| `origin` | [mohammadkhair7/khair-arabic-models](https://github.com/mohammadkhair7/khair-arabic-models) | `mohammadkhair7` |
| `qurancomp` | [qurancomp/khair-arabic-models](https://github.com/qurancomp/khair-arabic-models) | `qurancomp` |

**Every commit goes to both.** A plain `git push` cannot do this — each
repository is writable by a different GitHub account, and git presents only
one credential per host. Use the sync script, which pushes each remote with
its own account active via `gh auth switch` and then re-reads both remote
heads to prove they match:

```bash
python scripts/sync_repos.py            # push the current branch to both
python scripts/sync_repos.py --check    # verify only, push nothing
git sync                                # the same thing, if you set the alias
```

Set the alias once per clone:

```bash
git config alias.sync '!python scripts/sync_repos.py'
```

Both accounts need `gh auth login` (check with `gh auth status`). The script
restores whichever account was active before it ran, including after a
failure. If it reports `OUT OF SYNC`, fix that before doing anything else —
a half-published release is worse than an unpublished one.

## Secrets

The web app reads one credential, `SENDGRID_API_KEY`, and it must never be
committed. `.env` is git-ignored; `webapp/.env.example` is the only tracked
file that names these variables and it holds empty values. Enable the guard
once per clone:

```bash
git config core.hooksPath .githooks     # runs scripts/check_secrets.py
python scripts/check_secrets.py --all   # scan the whole tree
```

## What is most useful

In rough order of impact on the numbers in the paper:

1. **Near-duplicate removal before splitting.** Hash splitting separates
   identical strings only, and hadith recur across collections with small
   variations. This is the largest known source of optimism in every metric
   here, and nobody has measured how large.
2. **A gold evaluation set.** Every accuracy figure is agreement with a label
   source — the isnād parser, or the CAMeL analyzer. Even a few hundred
   human-checked units would turn "reproduces the rules" into "is correct."
3. **Position-level DER.** The claim that POS conditioning helps mainly on case
   endings is inferred from the design and prior work, not measured. A
   word-final vs. word-internal breakdown would settle it.
4. **Page-level structure training.** The structure model trains on single
   units but serves on 220-word page blocks. Training on realistic page context
   should close that gap.
5. **Dagger alef.** Adding U+0670 to the diacritic inventory needs a 17th class
   (or a second output head) plus retraining.

## Conventions

- Keep the code dependency-light. `torch` and `numpy` are the whole runtime;
  `camel-tools` is optional and only ever imported inside `teachers/`.
- Nothing in `src/arabicmodels/` may touch a database, a network, or a
  project-specific schema. Inference and training read files and write files.
- If you change a model, regenerate its metrics and update `docs/MODEL_CARDS.md`
  in the same commit. Metrics in the docs must come from an actual `eval` run.
- Checkpoints must stay loadable under `weights_only=True` — plain tensors,
  dicts and strings, no pickled objects.

## Reporting results

State which checkpoint, which split, and which command produced a number. Dev
figures are embedded in the checkpoints and were used for model selection, so
they are optimistic; test figures come from `eval` and are what belongs in
documentation.
