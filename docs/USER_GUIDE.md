# User Guide

*[النسخة العربية](USER_GUIDE.ar.md) · [English]*

Everything you need to **run**, **retrain** and **adapt** the four models, with
working examples. No prior machine-learning experience is assumed.

| Section | Read it if you want to… |
|---|---|
| [1. Setup](#1-setup) | install and check that everything works |
| [2. Running the models](#2-running-the-models-inference) | add diacritics, tag words, split text — the common case |
| [3. Your data](#3-your-data-formats-and-content) | understand what input the models and the trainer expect |
| [4. Training from scratch](#4-training-from-scratch) | build a model for a new language variety or new labels |
| [5. Fine-tuning](#5-fine-tuning) | adapt a shipped model to your own texts — usually what you want |
| [6. Scenarios](#6-use-case-scenarios) | follow a complete recipe for a real task |
| [7. Options reference](#7-options-reference) | look up a flag |
| [8. Troubleshooting](#8-troubleshooting) | fix an error message |

---

## 1. Setup

```bash
git clone https://github.com/khair/khair-arabic-models
cd khair-arabic-models
git lfs install && git lfs pull      # downloads the weights and datasets
pip install -e .
```

`git lfs pull` matters. Without it the `.pt` and `.jsonl` files are 130-byte
placeholders and every model will fail to load.

Check your installation:

```bash
arabicmodels info
```

You should see four models with `[x]`, their parameter counts, and whether you
have a GPU. A GPU makes things roughly ten times faster but nothing here
requires one.

### The three things the package gives you

| | What it does | Use it for |
|---|---|---|
| **Diacritizer** | adds the vowel marks (tashkīl) | reading aids, text-to-speech, search |
| **PosTagger** | labels each word's part of speech | linguistic analysis, filtering, indexing |
| **StructureTagger** | splits text into number / chain / body / heading | parsing hadith pages into records |

---

## 2. Running the models (inference)

### 2.1 Adding diacritics

The most common task. Two methods, and choosing correctly matters more than any
setting.

```python
from arabicmodels import Diacritizer

d = Diacritizer.load()          # loads the recommended grammar-aware model

d.diacritize("قال رسول الله صلى الله عليه وسلم")
# 'قَالَ رَسُولُ اللَّهِ صَلَّى اللَّهُ عَلَيْهِ وَسَلَّمَ'
```

**`diacritize()` rewrites every mark.** It removes whatever diacritics the text
already has and predicts all of them again. Use it on plain, unvocalized text.

**`fill_gaps()` only fills in the blanks.** Words that already carry marks are
left exactly as they are, and Qurʾānic citations are never touched:

```python
d.fill_gaps("قَالَ رسول الله ﴿إنا أعطيناك الكوثر﴾ وقال ابن عمر")
# 'قَالَ رَسُولُ اللهِ ﴿إنا أعطيناك الكوثر﴾ وَقَالَ ابْنُ عُمَرَ'
#  └ kept    └ filled in      └ untouched      └ filled in
```

> **Which one?** If your source is a printed edition whose vocalization was set
> by an editor, use `fill_gaps()`. An editor's marks are evidence; the model's
> are a guess. If your text has no marks at all, the two behave identically, so
> use `diacritize()`.

Choosing the model version:

```python
Diacritizer.load()             # v0.2, characters + grammar — recommended
Diacritizer.load(pos=False)    # v0.1, characters only — faster, less accurate
Diacritizer.load(device="cpu") # force CPU even if a GPU is present
```

v0.2 loads the POS tagger internally as a feature extractor, so it uses about
31 MB of memory instead of 20 MB and is somewhat slower. It is worth it: 4.46 %
error versus 5.12 %.

### 2.2 Tagging parts of speech

```python
from arabicmodels import PosTagger

t = PosTagger.load()
t.tag("حدثنا قتيبة بن سعيد قال حدثنا سفيان عن الزهري")
# [('حدثنا', 'verb'), ('قتيبة', 'noun_prop'), ('بن', 'noun'),
#  ('سعيد', 'noun_prop'), ('قال', 'verb'), ('حدثنا', 'verb'),
#  ('سفيان', 'noun_prop'), ('عن', 'prep'), ('الزهري', 'adj')]

t.tags              # the full list of 24 possible tags
t.tag_words([...])  # if your text is already split into words
```

### 2.3 Splitting text into its parts

```python
from arabicmodels import StructureTagger

s = StructureTagger.load()
unit = ("1248 - حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله قال حدثني نافع "
        "عن ابن عمر ان رسول الله صلى الله عليه وسلم قال من اقتنى كلبا الا كلب "
        "ماشية او ضاري نقص من عمله كل يوم قيراطان")

s.segments(unit)     # grouped into readable blocks
# [('HNUM',  '1248'),
#  ('ISNAD', '- حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله قال حدثني نافع عن ابن عمر ان'),
#  ('MATN',  'رسول الله صلى الله عليه وسلم قال من اقتنى كلبا الا كلب ماشية او ضاري نقص من عمله كل يوم قيراطان')]

s.tag(unit)          # one (word, label) pair per word
s.spans(page_text)   # [start, end, label] character offsets — for whole pages
```

Use `segments()` when you want to read the result, `tag()` when you want to
process word by word, and `spans()` when you need to highlight the original
text without altering it — the offsets point straight into the string you
passed in.

The four labels are `HNUM` (the printed number), `ISNAD` (the chain of
transmission), `MATN` (the report itself) and `HEADING` (a section title).

### 2.4 From the command line

For whole files, the CLI avoids writing any Python:

```bash
# add marks to a file, keeping any marks it already has
arabicmodels tashkeel annotate book.txt --pos --out book.vocalized.txt

# rewrite every mark instead
arabicmodels tashkeel infer --file book.txt --pos > book.vocalized.txt

# tag a file as word/tag pairs
arabicmodels pos infer --file corpus.txt > tagged.txt

# structure of several pages as JSON offsets
arabicmodels structure spans page1.txt page2.txt --out spans.json

# quick one-liner
arabicmodels tashkeel infer --text "قال رسول الله" --pos
```

`annotate` processes each file and applies the gap-only merge; `infer` rewrites
everything. Add `--no-protect-quran` to `annotate` if you *do* want marks added
inside ﴿…﴾ spans.

### 2.5 Speed

Load the model once and reuse it. The cost is in loading, not in running:

```python
d = Diacritizer.load()                    # do this once
results = [d.diacritize(t) for t in texts]  # not inside the loop
```

For very large jobs, use the CLI with many files at once, or pass
`device="cuda"` if you have a GPU.

---

## 3. Your data: formats and content

This section matters if you plan to train or fine-tune. Skip it if you only
want to run the shipped models.

All datasets are **JSONL**: one JSON object per line, UTF-8, no outer array,
no trailing commas.

### 3.1 The three formats

**Diacritization** — `tashkeel.jsonl`. One field: text *with* its marks. The
marks are the answer; the trainer strips them to make the question.

```json
{"text": "حَدَّثَنَا قُتَيْبَةُ بْنُ سَعِيدٍ قَالَ حَدَّثَنَا سُفْيَانُ", "split": "train"}
```

**Part of speech** — `pos.jsonl`. Words and their tags, same length, aligned
position by position.

```json
{"tokens": ["حدثنا", "قتيبة", "بن", "سعيد"], "tags": ["verb", "noun_prop", "noun", "noun_prop"], "split": "train"}
```

**Structure** — `indexing.jsonl`. Two kinds of row. A unit gives the character
offset where the chain ends and the body begins; a heading is a title where
every word gets the `HEADING` label.

```json
{"text": "1248 - حدثنا محمد بن بشار ... ان رسول الله قال من اقتنى كلبا", "sanad_end": 80, "split": "train"}
{"text": "باب فضل من استبرأ لدينه", "kind": "heading", "split": "train"}
```

`split` is `"train"`, `"dev"` or `"test"`. If you build data with the supplied
script it is filled in for you.

### 3.2 What the *content* has to look like

Format is easy to get right; content is where projects fail. Each task has a
real requirement:

| Task | Your text must… | Because |
|---|---|---|
| **Diacritization** | already be vocalized, and thoroughly | the marks *are* the training labels. Unvocalized text teaches nothing |
| **Structure** | be one hadith unit per record, with chains written in the usual formulae (حدثنا / أخبرنا / عن) | the labeler is a rule parser tuned to those formulae |
| **POS** | be ordinary running Arabic prose | the teacher analyzes word by word |

Concrete thresholds the builder enforces:

- **Diacritization:** a window is kept only if it has **≥ 60 Arabic letters**
  and **≥ 0.6 marks per letter**. A half-vocalized page is rejected. This is
  deliberate — training on sparse marks teaches the model to leave letters bare.
- **Structure:** records of **150–4,000 characters**, and the isnād parser must
  return confidence **≥ 0.9**. Typically 60–99 % of genuine hadith units pass,
  depending on the edition.
- **POS:** records of **200–2,500 characters** with at least 8 words.

How much do you need? Rough guidance, from the shipped datasets:

| Task | Shipped | Useful minimum for fine-tuning |
|---|---:|---:|
| Diacritization | 70,000 windows | ~2,000 windows |
| Structure | 48,000 rows | ~2,000 rows |
| POS | 4,000 passages | ~500 passages |

### 3.3 Building datasets from your own text

Put your text in a `.txt` file (or a folder of them), or a `.jsonl` of
`{"text": "..."}` objects, then:

```bash
# diacritization — from vocalized books
python scripts/build_datasets.py --task tashkeel \
    --input mybooks/ --record blank-line --out-dir mydata/

# structure — one unit per line, plus a file of section titles
python scripts/build_datasets.py --task indexing \
    --input units.jsonl --headings titles.txt --out-dir mydata/

# part of speech — needs the teacher installed
pip install "arabicmodels[teacher]" && camel_data -i morphology-db-msa-r13
python scripts/build_datasets.py --task pos --input units.jsonl --out-dir mydata/
```

`--record` tells the script how to cut a plain `.txt` file into records:

| `--record` | Each record is… | Good for |
|---|---|---|
| `line` (default) | one line | one unit per line |
| `blank-line` | a paragraph between blank lines | books with paragraph breaks |
| `file` | the whole file | one document per file |

The script reports what it kept, which is your first sanity check:

```
building tashkeel dataset (text as labeler)…
mydata/tashkeel.jsonl: 300 samples {'train': 260, 'test': 20, 'dev': 20}
```

If you get **0 samples**, your content failed the filters — for diacritization
that almost always means the text is not vocalized enough.

---

## 4. Training from scratch

Train from scratch only when your target is genuinely different: a new label
set, or a variety of Arabic far from classical hadith prose. Otherwise
[fine-tune](#5-fine-tuning) — it is faster and needs far less data.

```bash
arabicmodels structure train --epochs 3          # ~18 min on an RTX 3080
arabicmodels pos       train --epochs 4          # ~1 min
arabicmodels tashkeel  train --epochs 3          # ~7 min   (v0.1)
arabicmodels tashkeel  tag-data                  # prepares data for v0.2
arabicmodels tashkeel  train --pos --epochs 3    # ~7 min   (v0.2)
```

Order matters for v0.2: it uses the POS model's output as a feature, so train
POS first, then `tag-data`, then `--pos`.

On your own data, and writing somewhere safe:

```bash
arabicmodels tashkeel train \
    --data mydata/tashkeel.jsonl \
    --out  mymodels/tashkeel.pt \
    --epochs 3
```

> **Warning.** Without `--out`, training **overwrites the shipped checkpoint**
> in `models/`. Always pass `--out` unless you intend to replace it. If you
> overwrite one by accident: `git checkout models/`.

What a healthy run looks like — loss falling, dev metric improving:

```
device=cuda data=tashkeel.jsonl train=63015 dev=3445 vocab=77 params=5,146,256
checkpoint -> mymodels/tashkeel.pt
epoch 1: loss 0.2318 dev DER(all) 0.0705 DER(marked) 0.0602 (132s)
epoch 2: loss 0.1487 dev DER(all) 0.0563 DER(marked) 0.0471 (131s)
epoch 3: loss 0.1252 dev DER(all) 0.0502 DER(marked) 0.0409 (130s)
  saved -> mymodels/tashkeel.pt
```

Only the best epoch is saved, so a bad last epoch cannot spoil your checkpoint.

Then measure on data the model never saw:

```bash
arabicmodels tashkeel eval --data mydata/tashkeel.jsonl --ckpt mymodels/tashkeel.pt
```

---

## 5. Fine-tuning

**This is usually what you want.** Fine-tuning starts from a shipped model and
nudges it toward your texts. It needs a fraction of the data and a fraction of
the time, and it keeps everything the model already learned about Arabic.

### 5.1 The one command

```bash
arabicmodels tashkeel train \
    --init-from models/tashkeel_bilstm.pt \
    --data      mydata/tashkeel.jsonl \
    --out       mymodels/tashkeel-mine.pt \
    --epochs 2 --lr 2e-4
```

Three things to notice:

- `--init-from` is what turns training into fine-tuning.
- `--out` keeps the original safe.
- `--lr 2e-4` is **ten times lower** than the from-scratch rate. Fine-tuning
  with a high rate erases what the model knew. This is the single most common
  mistake.

### 5.2 Why it works — measured

The same 2,257 training rows, one epoch, everything else equal:

| | Vocabulary | Dev error (DER) |
|---|---:|---:|
| From scratch | 64 characters, rebuilt from the small set | **24.30 %** |
| Fine-tuned from the shipped model | 77 characters, reused | **3.08 %** |

An eightfold difference from one flag. Small datasets cannot teach Arabic from
nothing; they can only adjust a model that already knows it.

Note the vocabulary column. When you fine-tune, the character and tag
vocabularies come **from the checkpoint**, not from your data — an embedding
learned for one vocabulary is meaningless against another. The tool prints a
line confirming this:

```
fine-tuning from models/tashkeel_bilstm.pt — vocabularies reused from the
checkpoint, not rebuilt from the data
```

A practical consequence: characters your data has but the checkpoint's
vocabulary lacks become `<unk>`. For POS, tags outside the original 24 cannot
be learned at all, and the trainer warns you:

```
warning: 2 tag(s) absent from the vocabulary will train as <unk>: ['dialect', 'foreign']
```

If you need new tags, train from scratch instead.

### 5.3 Settings that work

| Setting | From scratch | Fine-tuning |
|---|---|---|
| `--lr` | 1e-3 (2e-3 for tashkeel) | **2e-4**, or 1e-4 for very small data |
| `--epochs` | 3–4 | **1–3** — more will overfit |
| Data needed | tens of thousands of rows | ~2,000 rows is often enough |
| `--init-from` | omit | **required** |
| `--out` | recommended | **required in practice** |

Watch the dev metric between epochs. If it stops improving, stop — the best
epoch is already saved.

### 5.4 Fine-tuning the other two models

```bash
# structure, on your own annotated units
arabicmodels structure train --init-from models/indexing_wordtagger.pt \
    --data mydata/indexing.jsonl --out mymodels/structure-mine.pt \
    --epochs 2 --lr 2e-4

# part of speech
arabicmodels pos train --init-from models/pos_wordtagger.pt \
    --data mydata/pos.jsonl --out mymodels/pos-mine.pt \
    --epochs 2 --lr 2e-4
```

### 5.5 Using your fine-tuned model

From the command line, point `--ckpt` at it:

```bash
arabicmodels tashkeel infer --text "قال رسول الله" --ckpt mymodels/tashkeel-mine.pt
arabicmodels tashkeel eval  --data mydata/tashkeel.jsonl --ckpt mymodels/tashkeel-mine.pt
```

From Python, pass `path=`:

```python
d = Diacritizer.load(pos=False, path="mymodels/tashkeel-mine.pt")
s = StructureTagger.load(path="mymodels/structure-mine.pt")
p = PosTagger.load(path="mymodels/pos-mine.pt")
```

For a fine-tuned **v0.2** diacritizer you can also swap the POS feature
extractor with `--pos-ckpt`.

---

## 6. Use-case scenarios

### Scenario A — Vocalize a book that is partly vocalized already

*You have a printed edition where the editor marked some words. You want the
rest filled in without losing the editor's work.*

```bash
arabicmodels tashkeel annotate chapter1.txt chapter2.txt \
    --pos --out vocalized.txt
```

`annotate` fills only fully bare words and leaves Qurʾānic citations alone.
Verify before trusting the batch:

```python
from arabicmodels import Diacritizer, split_marks
d = Diacritizer.load()
original = open("chapter1.txt", encoding="utf-8").read()
result = d.fill_gaps(original)
assert split_marks(result)[0] == split_marks(original)[0]  # no letter changed
```

That assertion is worth running on any batch: it proves only marks moved.

### Scenario B — Adapt the diacritizer to a different kind of text

*Your corpus is classical but not hadith — fiqh, tafsīr, poetry — and accuracy
is disappointing.*

1. Collect vocalized text from that genre; aim for 2,000+ windows.
2. Build the dataset:
   ```bash
   python scripts/build_datasets.py --task tashkeel \
       --input myfiqh/ --record blank-line --out-dir mydata/
   ```
3. Fine-tune gently:
   ```bash
   arabicmodels tashkeel train --init-from models/tashkeel_bilstm.pt \
       --data mydata/tashkeel.jsonl --out mymodels/fiqh.pt --epochs 2 --lr 2e-4
   ```
4. Compare old and new on **your** test split:
   ```bash
   arabicmodels tashkeel eval --data mydata/tashkeel.jsonl   # shipped model
   arabicmodels tashkeel eval --data mydata/tashkeel.jsonl --ckpt mymodels/fiqh.pt
   ```

If the fine-tuned number is not better, you likely used too high a learning
rate or too many epochs.

### Scenario C — Turn page scans into structured records

*You have OCR'd pages and want each hadith as a record with its chain and body
separated.*

```python
from arabicmodels import StructureTagger

s = StructureTagger.load()
page = open("page_042.txt", encoding="utf-8").read()

for start, end, label in s.spans(page):
    print(label, repr(page[start:end][:60]))
```

`spans()` gives character offsets into the original page, so you can store the
structure alongside the untouched text rather than rewriting it. To skip front
matter and index pages automatically:

```python
from arabicmodels.indexing import usable_spans
spans = s.spans(page)
if usable_spans(spans):
    ...   # a real hadith page
```

Pages are processed in 220-word blocks, so expect the occasional error right at
a block boundary on very long pages.

### Scenario D — Build a searchable, grammatically aware index

*You want search that can distinguish the verb صام from a proper noun.*

```python
from arabicmodels import PosTagger, normalize_arabic

tagger = PosTagger.load()
for doc_id, text in corpus:
    for word, tag in tagger.tag(text):
        index.add(doc_id, normalize_arabic(word), tag)
```

`normalize_arabic` folds the spelling variants (أ إ آ → ا, ة → ه, ى → ي) so
that a query matches regardless of how it was typed.

### Scenario E — Train on a corpus that is not hadith at all

*You have a large vocalized classical corpus of another kind.*

Diacritization transfers well — the marks are the labels regardless of genre,
so build a dataset and fine-tune (Scenario B). Structure does **not** transfer:
its labeler is a hadith isnād parser and will find nothing in other genres. To
segment a different kind of document you would need your own boundary labels in
`indexing.jsonl` format, then train from scratch.

### Scenario F — Run on a CPU-only server

Everything works unchanged; expect roughly ten times slower.

```python
d = Diacritizer.load(pos=False, device="cpu")   # v0.1 is the lighter choice
```

For batch jobs, prefer the CLI over a Python loop and pass many files at once
so the model loads only once. Memory: about 20 MB for v0.1, 31 MB for v0.2.

---

## 7. Options reference

### Common to every `train`

| Option | Default | Meaning |
|---|---|---|
| `--data JSONL` | the shipped dataset | training data |
| `--out CKPT` | **overwrites the shipped checkpoint** | where to save |
| `--init-from CKPT` | off | fine-tune from this checkpoint |
| `--epochs N` | 3 (POS: 4) | passes over the data |
| `--batch-size N` | 32 (tashkeel: 64) | lower it if you run out of memory |
| `--lr FLOAT` | 1e-3 (tashkeel: 2e-3) | use 2e-4 when fine-tuning |
| `--limit N` | all | read only the first N rows — good for a quick test |
| `--seed N` | 13 | random seed |

### `eval`

| Option | Meaning |
|---|---|
| `--data JSONL` | evaluate on your own test split |
| `--ckpt CKPT` | evaluate a specific checkpoint |
| `--limit N` | read only the first N rows |

### `infer` / `annotate` / `spans`

| Option | Applies to | Meaning |
|---|---|---|
| `--text "..."` | infer | inline text |
| `--file PATH` | infer | read from a file |
| `--out PATH` | annotate, spans | write results to a file |
| `--ckpt CKPT` | all | use your own model |
| `--pos` | tashkeel | use the grammar-aware v0.2 |
| `--pos-ckpt CKPT` | tashkeel | your own POS feature extractor |
| `--no-protect-quran` | annotate | also mark inside ﴿…﴾ spans |

### Python API

| Call | Returns |
|---|---|
| `Diacritizer.load(pos=True, device=None, path=None)` | a diacritizer |
| `.diacritize(text)` | text with all marks recomputed |
| `.fill_gaps(text, protect_quran=True)` | text with only bare words filled |
| `PosTagger.load(device=None, path=None)` | a tagger |
| `.tag(text)` / `.tag_words(words)` | `[(word, tag)]` / `[tag]` |
| `.tags` | the 24 possible tags |
| `StructureTagger.load(device=None, path=None)` | a segmenter |
| `.tag(text)` / `.segments(text)` / `.spans(text)` | per word / grouped / offsets |
| `normalize_arabic(text)` | spelling variants folded |
| `split_marks(text)` / `apply_marks(text, labels)` | marks off / marks back on |
| `parse_isnad(text)` | the rule-based chain parse |

---

## 8. Troubleshooting

**`checkpoint not found` / `FileNotFoundError`**
The weights were not downloaded. Run `git lfs install && git lfs pull`, then
`arabicmodels info` to confirm.

**`no usable training rows in …`**
Your data failed the filters. For diacritization the text is probably not
vocalized enough (needs ≥ 0.6 marks per letter); for structure the records may
not be single hadith units. Re-read [section 3.2](#32-what-the-content-has-to-look-like).

**`--pos needs rows with a 'tags' field`**
The v0.2 diacritizer needs POS-tagged data. Run
`arabicmodels tashkeel tag-data` first.

**`build_datasets.py` produced 0 samples**
Same cause as above. Print a couple of your records and check them against the
examples in [section 3.1](#31-the-three-formats).

**`CAMeL teacher unavailable`**
Only the POS dataset builder needs it:
`pip install "arabicmodels[teacher]"` then `camel_data -i morphology-db-msa-r13`.

**Fine-tuning made the model worse**
Learning rate too high or too many epochs. Try `--lr 1e-4 --epochs 1`. Check
the dev metric after each epoch rather than only at the end.

**Out of memory on the GPU**
Lower `--batch-size` (try 16, or 32 for tashkeel), or add `--limit` while
experimenting.

**Output looks right but does not compare equal to my reference**
Marks are emitted shadda-first, which renders identically to the canonical
order but is a different byte sequence. Normalize both sides:
`unicodedata.normalize("NFC", text)`.

**A word lost its dagger alef (هَٰذَا → هَذَا)**
Expected. U+0670 is outside the 16-class inventory and the models never
produce it. See [`MODEL_CARDS.md`](MODEL_CARDS.md).

**I overwrote a shipped checkpoint**
`git checkout models/` restores it.

---

## Where to go next

- [`MODEL_CARDS.md`](MODEL_CARDS.md) — what each model does and does not do
- [`DATA.md`](DATA.md) — dataset provenance and filters in detail
- [`TRAINING.md`](TRAINING.md) — hyperparameters and architecture
- [`PAPER.md`](PAPER.md) — the full method and results
