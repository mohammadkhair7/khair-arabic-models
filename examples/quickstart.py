"""Run all four models on one hadith unit.

    python examples/quickstart.py
"""
from arabicmodels import Diacritizer, PosTagger, StructureTagger, parse_isnad

UNIT = ("1248 - حدثنا محمد بن بشار قال حدثنا يحيى عن عبيد الله قال حدثني نافع "
        "عن ابن عمر ان رسول الله صلى الله عليه وسلم قال من اقتنى كلبا الا كلب "
        "ماشية او ضاري نقص من عمله كل يوم قيراطان")


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


rule("1. Structure segmentation (model A)")
structure = StructureTagger.load()
for label, text in structure.segments(UNIT):
    print(f"[{label:7s}] {text}")

rule("2. Part-of-speech tagging (model C)")
tagger = PosTagger.load()
print(" ".join(f"{w}/{t}" for w, t in tagger.tag(UNIT)[:12]), "…")

rule("3. Diacritization (model B2, grammar-aware)")
diacritizer = Diacritizer.load(pos=True)
print(diacritizer.diacritize(UNIT))

rule("4. Gap-only merge — existing marks and Qurʾān spans are preserved")
partial = "قَالَ رسول الله ﴿إنا أعطيناك الكوثر﴾ وقال ابن عمر"
print("in :", partial)
print("out:", diacritizer.fill_gaps(partial))

rule("5. The rule-based labeler behind model A")
parsed = parse_isnad(UNIT)
print(f"boundary at char {parsed.sanad_end_raw}, confidence {parsed.confidence}")
print("narrators:", [h.mention for h in parsed.hops])
