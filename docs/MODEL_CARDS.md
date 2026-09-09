# Model cards

Four checkpoints, each a single self-contained `torch.save` dictionary holding
the weights, the vocabularies needed to encode input, the dev metrics recorded
when that checkpoint was selected, and a task/version string. There are no
companion config or tokenizer files.

Common to all four:

- **Architecture:** bidirectional LSTM sequence labelers. No pretrained
  transformer, no external embeddings.
- **Training:** AdamW, gradient clipping at 2.0, seed 13, single run each.
  Checkpoint selected on the best dev metric.
- **Runtime:** CPU-friendly; roughly 10× faster on a GPU.
- **Language:** classical Arabic (hadith prose). Not tuned for MSA news,
  dialect, or poetry.
- **Evaluation caveat:** each model is scored against **its own label source**.
  See "What the number does not say" in each card.

Inspect any checkpoint with `arabicmodels info`.

---

## Model A — structure segmentation

`models/indexing_wordtagger.pt` · v0.2 · 2,836,678 parameters · 10.8 MB

**Task.** Label every word of running hadith text as one of four classes:

| Class | Meaning |
|---|---|
| `HNUM` | the printed hadith number, e.g. «1248 -» |
| `ISNAD` | the chain of transmission (sanad) |
| `MATN` | the body of the report |
| `HEADING` | a كتاب / باب / فصل section title |

This is classification, not generation: the source text is never rewritten, so
every decision is auditable and reversible.

**Input.** Whitespace-tokenized text, up to 220 words per sequence. Raw surface
forms, except that **diacritics are stripped before encoding** — see the
shortcut below. Nothing else is normalized: the hamza carriers, tāʾ marbūṭa and
alif maqṣūra reach the character encoder as written, because folding them
measurably moves the chain/body boundary. The caller's text is untouched; only
the encoder sees the bare skeleton.

**Output.** One label per word, or `[start, end, label]` character spans via
`StructureTagger.spans()`.

**Labels from.** The rule-based isnād parser at confidence ≥ 0.9, plus section
titles. See [`DATA.md`](DATA.md).

**Results** (test split: 174,404 words, 2,033 boundaries):

| Metric | Value |
|---|---:|
| Word accuracy | **99.96 %** |
| Chain/body boundary within ±2 words | **99.75 %** |
| Median boundary error | 0 words |
| Dev accuracy, epoch 1 → 3 | 97.97 % → 99.95 % |

**What the number does not say.** 99.96 % measures how faithfully the model
reproduces the *rule parser*, on the subset of units where that parser was
already confident. It is not evidence of agreement with a human editor. The
value of the model is generalization — it applies the same convention to text
the rules parse poorly — but that generalization is precisely what these
figures do not measure.

**Known weakness — the model learned a shortcut for `HEADING`.** In the source
books the كتاب / باب titles are vowelled and the running text around them is
not, so "carries diacritics" and "is a heading" almost never disagree, and the
model learned the cheaper feature. Fed a fully vowelled isnād it answers
`HEADING` for every word. The effect is graded, not a cliff: vowelling a
correct `ISNAD` unit word by word, the labels hold until roughly a third of the
characters are marks and then flip wholesale.

This is why the encoder is now shown the stripped skeleton, which is what the
`ISNAD` and `MATN` training text looked like. It is a mitigation and not a
repair — the model still cannot use diacritics as evidence, when for a heading
they genuinely are some. The repair is to strip marks in `_prepare` as well and
retrain, so the distinction has to be learned from the words; that needs the
corpus and a fresh set of numbers, so it is deliberately not folded in here.

The figures above are unaffected: the test split is already unvowelled, so
stripping is a no-op over it. Equally, they never measured this failure — which
is the point worth keeping. A held-out set drawn from the same unvowelled text
cannot see a shortcut that only fires on vowelled input, and the web app's
ordinary input is a vowelled edition.

**Known weakness — page-level application.** Trained on single hadith units,
applied at serving time to whole pages in consecutive 220-word blocks. Block
boundaries are where errors concentrate, and no metric here covers that
mismatch. `usable_spans()` exists to skip pages (front matter, indexes) that
contain no real hadith anatomy.

**Known weakness — a matn with no isnād.** Training units are either all
`HEADING` or a chain followed by a body, so a bare report quoted on its own is
out of distribution and tends to come back `HEADING`. Pass whole units, chain
included, when you have them.

---

## Model B1 — diacritization, characters only

`models/tashkeel_bilstm.pt` · v0.1 · 5,146,256 parameters · 19.6 MB

**Task.** For each Arabic letter, predict one of 16 classes: eight vowel states
(none, fatḥa, ḍamma, kasra, sukūn, fatḥatān, ḍammatān, kasratān) × with or
without shadda.

Because the model only *classifies* letters and never generates them, the
output is guaranteed to differ from the input in combining marks alone. It
cannot drop, insert, or substitute a letter.

**Input.** Bare (or partially marked) Arabic text, character-level, in windows
of ≈ 380 characters — matching the training window, which is what inference
chunks to.

**Output.** The same text with diacritics applied.

**Labels from.** Vocalized classical prints. Stripping the marks yields the
input; the marks are the target.

**Results** (test split: 3,540 windows, ≈ 605k scored letters):

| Metric | Value |
|---|---:|
| DER (all letters) | 5.12 % |
| DER (marked letters) | 4.16 % |

**Ships as the ablation.** B1 exists so the effect of grammar conditioning in
B2 can be measured against an otherwise identical model — same data, same
splits, same budget. For production use, prefer B2.

---

## Model B2 — diacritization, grammar-aware **(recommended)**

`models/tashkeel_bilstm_pos.pt` · v0.2 · 5,245,328 parameters · 20.0 MB

**Task.** As B1, with every character embedding concatenated with a
32-dimensional embedding of its word's POS tag. The tag comes from model C run
on the bare text, so the vowel choice — case endings above all — is conditioned
on grammar rather than letter context alone.

**Input.** Bare Arabic text. Loading B2 also loads model C automatically; the
two run as one pipeline.

**Results** (identical data, splits and budget as B1):

| Metric | B1 | **B2** | Relative reduction |
|---|---:|---:|---:|
| DER (all letters) | 5.12 % | **4.46 %** | 12.9 % |
| DER (marked letters) | 4.16 % | **3.51 %** | 15.6 % |

About 4,000 fewer mis-marked letters out of 605k, at no extra training cost.
Since the POS tags are themselves silver (teacher → student → features), this
understates what gold grammatical input would give.

**Reading the two DER columns.** Classical prints are *selectively* vocalized —
editors mark what a reader might get wrong and leave the rest bare. DER(all)
penalizes the model for marking a letter the editor deliberately left alone,
even when the mark is linguistically correct, so it is pessimistic. DER(marked)
scores only letters the book itself marked, so it is optimistic. True accuracy
is between them: roughly 3.5–4.5 errors per hundred letters.

**Limitations.**

- **16 classes only.** Superscript (dagger) alef U+0670 is outside the
  inventory and is never produced: `هَٰذَا` comes back as `هَذَا`. Words like
  ذٰلك and الرحمٰن are affected.
- **Mark ordering.** Output is shadda-first (`0651 064E`); Unicode canonical
  order is the reverse. Identical on screen, not byte-identical. Compare with
  `unicodedata.normalize("NFC", …)`.
- **Whole-text rewriting.** `diacritize()` recomputes every mark, discarding
  the source vocalization. Where that vocalization has editorial authority, use
  `fill_gaps()`, which only fills fully bare words and never enters a Qurʾānic
  citation span.

---

## Model C — part-of-speech tagger

`models/pos_wordtagger.pt` · v0.1 · 2,846,170 parameters · 10.9 MB

**Task.** One of 24 tags per word (`verb`, `noun`, `noun_prop`, `adj`, `prep`,
`digit`, `punc`, …). Read the full inventory from a loaded model with
`PosTagger.load().tags`.

**Input.** Whitespace-tokenized text, up to 120 words per sequence.

**Distilled from.** The CAMeL Tools morphological analyzer. The student needs
no morphology database at inference, which is what makes it fast enough to tag
a whole corpus and small enough to embed inside B2 as a feature extractor.

**Results** (test split: 19,025 tokens):

| Metric | Value |
|---|---:|
| Agreement with teacher | **95.87 %** |
| Dev agreement, epoch 1 → 4 | 84.59 % → 95.75 % |

Most frequent disagreements: `noun → verb` (175), `noun_prop → noun` (148),
`verb → noun` (118), `adj → noun` (117).

**What the number does not say.** 95.87 % is agreement with a context-free,
MSA-trained teacher — not linguistic correctness. The student is a
sentence-level BiLSTM and therefore sees *more* context than its teacher did,
so some of the 4.13 % "disagreement" is very likely the student being right.
Nothing here distinguishes the two cases; that would need gold classical
annotation, which does not exist for this corpus.

---

## Intended and unintended use

**Intended.** Research on classical Arabic NLP; reading aids and search over
hadith and comparable classical corpora; a strong, cheap baseline for
diacritization; a starting point for fine-tuning on related domains.

**Not intended.** Anything where a wrong diacritic changes a legal or doctrinal
reading without a human in the loop. At 3.5–4.5 % DER these models are reading
aids, not editors. The structure model likewise reproduces a rule parser's
conventions, not a scholar's judgment about where a chain ends.
