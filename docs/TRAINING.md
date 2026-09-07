# Training

Every model uses the same loop: build vocabularies from the training split,
train with AdamW and cross-entropy, evaluate on dev after each epoch, and keep
the checkpoint with the best dev metric. Nothing is pretrained and nothing is
downloaded at train time.

## Requirements

```bash
pip install -e ".[dev]"
git lfs pull                       # the datasets under data/
```

A GPU is strongly recommended but not required — the models are small enough
that CPU training is slow rather than impossible. Reference timings below are
from one NVIDIA RTX 3080 (12 GB), CUDA 12.4, PyTorch 2.6.0+cu124.

## Reproducing the four checkpoints

Order matters: B2 consumes model C's output, so C must exist before
`tag-data` runs.

```bash
arabicmodels structure train --epochs 3          # ~18 min  -> indexing_wordtagger.pt
arabicmodels pos       train --epochs 4          # ~1 min   -> pos_wordtagger.pt
arabicmodels tashkeel  train --epochs 3          # ~7 min   -> tashkeel_bilstm.pt
arabicmodels tashkeel  tag-data                  #          -> data/tashkeel_pos.jsonl
arabicmodels tashkeel  train --pos --epochs 3    # ~7 min   -> tashkeel_bilstm_pos.pt
```

Roughly 5.5 min/epoch for structure, 15 s/epoch for POS, 2.2 min/epoch for each
diacritizer. The whole family is about half an hour.

**Without `--out`, training overwrites the checkpoint in `models/`** whenever a
run beats its own previous best dev score. Pass `--out mymodels/x.pt` to write
somewhere else; `git checkout models/` restores an overwritten one.

To adapt a shipped model to a different corpus rather than retrain it, use
`--init-from` — see [`USER_GUIDE.md` §5](USER_GUIDE.md#5-fine-tuning). It needs
far less data and a learning rate about ten times lower.

Then confirm you landed where the paper did:

```bash
arabicmodels structure eval      # 99.96 % word acc · 99.75 % boundary ±2w
arabicmodels pos       eval      # 95.87 % agreement
arabicmodels tashkeel  eval      # 5.12 % DER(all) · 4.16 % DER(marked)
arabicmodels tashkeel  eval --pos# 4.46 % DER(all) · 3.51 % DER(marked)
```

Runs are single-seed (13); no seed variance was measured, so treat small
differences as noise of unknown size.

## Hyperparameters

| | A. Structure | C. POS | B1 / B2. Diacritization |
|---|---|---|---|
| Optimizer | AdamW, PyTorch defaults | same | same |
| Learning rate (constant) | 1e-3 | 1e-3 | 2e-3 |
| Batch size, train / eval | 32 / 64 | 32 / 64 | 64 / 128 |
| Epochs | 3 | 4 | 3 |
| Loss | cross-entropy, pad ignored | same | cross-entropy, pad **and non-Arabic characters** ignored |
| Gradient clipping | global norm 2.0 | same | same |
| Dropout | 0.2 between BiLSTM layers | same | same |
| Max sequence | 220 words × 18 chars | 120 words | 380 characters |
| Character vocabulary | 83 | 71 | 77 |
| Checkpoint selected on | best dev word accuracy | best dev accuracy | lowest dev DER(all) |
| Seed | 13 | 13 | 13 |

Useful flags on every `train` command: `--epochs`, `--batch-size`, `--lr`,
`--seed`, `--data` and `--out`, plus `--limit N` to read only the first N rows —
handy for a 30-second smoke test before committing to a full run. `--init-from`
switches to fine-tuning, which reuses the source checkpoint's vocabularies
instead of rebuilding them from the data. The full option reference is in
[`USER_GUIDE.md` §7](USER_GUIDE.md#7-options-reference).

## Architectures

**`WordTagger`** (models A and C) is two levels of BiLSTM. The first encodes
each word from its characters — a 64-d character embedding into a 128-d
bidirectional LSTM, whose final states concatenate into a 256-d word vector.
This is why unseen words still get useful representations: rare classical forms
and unfamiliar proper names are read from their letter patterns. The second
level runs a 2-layer, 256-d bidirectional LSTM over the word vectors and a
linear head classifies each position.

**`TashkeelNet`** (B1) embeds characters at 128 dimensions, runs a 2-layer,
384-d bidirectional LSTM, and predicts one of 16 classes per character.
**`TashkeelPosNet`** (B2) is identical except each character embedding is
concatenated with a 32-d embedding of its word's POS tag before the LSTM —
about 99k extra parameters for a ~13 % relative error reduction.

Loss is masked to Arabic letters only, so punctuation, digits and Latin text
neither contribute gradient nor inflate the metrics.

## Training on your own corpus

Build datasets from ordinary text files, then train exactly as above:

```bash
python scripts/build_datasets.py --task tashkeel --input mycorpus/ \
    --record blank-line --out-dir data/
arabicmodels tashkeel train --epochs 3
```

See [`DATA.md`](DATA.md) for input formats, the filters applied, and two
deliberate differences between this script and the one that produced the
shipped datasets.

Three things to know before you do:

- **Diacritization needs vocalized source text.** The whole method rests on the
  text being its own answer key. The builder rejects windows below 0.6 marks
  per Arabic letter, and if your corpus is unvocalized it will produce nothing.
- **Structure expects one hadith unit per record**, and the isnād parser is
  tuned to hadith transmission formulae. On other genres it will report low
  confidence and yield few rows — which is the honest outcome, not a bug.
- **POS needs the teacher installed:**
  ```bash
  pip install "arabicmodels[teacher]"
  camel_data -i morphology-db-msa-r13
  ```

## Checkpoint format

One `torch.save` dictionary, no side files:

```python
{
  "state_dict": ...,      # weights
  "vocab" | "char_vocab": {...},   # character vocabulary
  "tag_vocab": {...},     # label inventory (absent for B1)
  "metrics": {...},       # dev metrics at the moment of selection
  "task": "tashkeel",     # indexing | pos | tashkeel
  "version": "0.2",
}
```

Note that the embedded `metrics` are **dev** figures, recorded when the
checkpoint was chosen. The test figures quoted throughout the documentation come
from the `eval` commands, which score the held-out test split.

Checkpoints load under `weights_only=True`, so they execute no pickled code.
