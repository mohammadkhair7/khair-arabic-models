# Datasets

The four files under `data/` are the exact data the shipped checkpoints were
trained on — not a sample or a reconstruction.

| File | Rows | train / dev / test | Size | Row shape |
|---|---:|---|---:|---|
| `indexing.jsonl` | 48,000 | 43,209 / 2,348 / 2,443 | 28.3 MB | `{text, sanad_end, split}` ×40,000 and `{text, kind:"heading", split}` ×8,000 |
| `pos.jsonl` | 4,000 | 3,637 / 182 / 181 | 7.5 MB | `{tokens[], tags[], split}` |
| `tashkeel.jsonl` | 70,000 | 63,015 / 3,445 / 3,540 | 46.6 MB | `{text, split}` |
| `tashkeel_pos.jsonl` | 70,000 | 63,015 / 3,445 / 3,540 | 73.6 MB | `{text, tags[], split}` |

No row carries an identifier, a source reference or any other metadata — only
the text, its labels, and its split assignment.

---

## Provenance

The text was harvested from a corpus of **659,000 passages across 33 hadith
collections**, assembled from digitized page archives of the Shamela library
and a structured hadith database, and held in PostgreSQL as part of a private
project. That project is not open source; these datasets, the models and the
tooling here are.

The works themselves — the canonical hadith collections and their classical
commentaries — are many centuries old and in the public domain. What may carry
rights in some jurisdictions is the *modern digitization and critical edition*
each text was typed from. The rows here are short, non-contiguous excerpts
(≤ 380 characters for diacritization, single units elsewhere) retained so the
results in `PAPER.md` are reproducible. If you plan to redistribute the data
commercially, check the standing of the source editions where you are.

### Diacritization source

Densely vocalized editions were preferred, led by the Dār al-Shaʿb printing of
Ṣaḥīḥ al-Bukhārī (fully vocalized throughout), then up to 3,000 random passages
per remaining edition until 70,000 qualifying windows were collected. This is
why the diacritization data leans toward Bukhārī.

---

## How each label set was produced

### `tashkeel.jsonl` — the text is the labeler

No annotation step exists. A vocalized print already contains the answer:

1. Strip viewer artefacts (`AddHistory(...)`, `[12/345]` page stamps).
2. Cut into windows of ≤ 380 characters on word boundaries.
3. Keep a window only if it has **≥ 60 Arabic letters** and a **mark density
   ≥ 0.6** (diacritics per Arabic letter). This is the quality gate: it admits
   only text a careful editor vocalized thoroughly, and rejects the partially
   marked majority.
4. At training time `split_marks` peels the marks off. The peeled text is the
   input; the marks are the target.

The density filter is applied **per window**, not per passage, so a lightly
vocalized book still contributes its well-marked passages.

### `indexing.jsonl` — the rules are the labeler

Chain/body boundaries come from the rule-based isnād parser shipped here as
[`src/arabicmodels/isnad.py`](../src/arabicmodels/isnad.py). Filters:

- the parser's first chain per unit (`ord = 0`), **confidence ≥ 0.9**,
- boundary offset > 30 characters (rejects degenerate parses),
- unit length between 150 and 4,000 characters,
- after labeling, keep units of ≥ 6 words containing **both** an ISNAD and a
  MATN word.

Word labels follow from the boundary: a leading numeral becomes `HNUM`, words
before the boundary `ISNAD`, words after it `MATN`.

The 8,000 `HEADING` rows are section titles (كتاب / باب / فصل) of 15–200
characters, every word labeled `HEADING`.

Keeping only confidence ≥ 0.9 is the point of the exercise: the network learns
the convention from the cases the rules are sure about, then applies it to the
text the rules stumble on.

### `pos.jsonl` — the engine is the labeler

4,000 units of 200–2,500 characters were tagged by the CAMeL Tools
morphological analyzer (`calima-msa-r13`, `NOAN_PROP` backoff), one analysis
per whitespace token, keeping units of ≥ 8 tokens. Two properties of this
teacher propagate into the data and must be kept in mind when reading the
95.87 % agreement figure:

- it is **context-free** — analyses are ranked by frequency and the first is
  taken, so the sentence does not influence the tag;
- its database targets **Modern Standard Arabic**, and its backoff labels
  anything unanalyzable as a proper noun, which inflates `noun_prop` on
  classical vocabulary.

### `tashkeel_pos.jsonl` — derived

`tashkeel.jsonl` plus one field: the POS student's tag for each word of the
bare text. Regenerate it any time with `arabicmodels tashkeel tag-data`
(a few minutes on a GPU) rather than downloading 74 MB.

---

## Splits

Rows are assigned **90 % train, 5 % dev, 5 % test** by `md5(key) % 100`
(`< 90` train, `< 95` dev, else test). Deterministic across
machines and runs, and identical keys can never land in different splits.

The key differs by dataset, and this matters:

| Dataset | Hash key used for the shipped data |
|---|---|
| `tashkeel.jsonl` | the window text itself |
| `indexing.jsonl` (units) | the source passage id |
| `indexing.jsonl` (headings) | the source TOC-node id |
| `pos.jsonl` | the source passage id |

**Hashing separates identical strings, not similar ones.** Hadith recur across
collections with small variations in wording and chain, so some test material
closely resembles training material. The reported metrics are therefore
optimistic by an amount nobody has measured. Removing near-duplicates before
splitting is the single most valuable improvement available to this data.

---

## Rebuilding from your own corpus

[`scripts/build_datasets.py`](../scripts/build_datasets.py) applies the same
filters to ordinary files instead of the private database:

```bash
# Diacritization — needs text that is already vocalized
python scripts/build_datasets.py --task tashkeel --input corpus/ --record blank-line

# Structure — one hadith unit per record; headings supplied separately
python scripts/build_datasets.py --task indexing --input units.jsonl \
    --headings section_titles.txt

# POS — needs the teacher: pip install "arabicmodels[teacher]"
#                          camel_data -i morphology-db-msa-r13
python scripts/build_datasets.py --task pos --input units.jsonl
```

Input may be a `.jsonl` file of `{"text": "..."}` objects, a `.txt` file, or a
directory of either. `--record` controls how plain text is divided into records
(`line`, `blank-line`, or `file`).

### Two deliberate differences from the original builder

1. **Split keys.** A generic corpus has no passage ids, so the script hashes
   the **record text** for every dataset. The shipped `indexing.jsonl` and
   `pos.jsonl` were split on database ids instead. Splits produced by this
   script therefore will not match the shipped files row for row — the
   *procedure* is reproduced, not the exact partition.
2. **Boundary source.** The original read boundaries precomputed by the isnād
   parser during ingestion; this script runs the same parser inline. Results
   are equivalent for the first chain of each unit.

Sampling also differs: the original drew randomly from the database, while the
script consumes records in input order until it hits the target count. Shuffle
your input if you want a random sample.
