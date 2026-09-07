"""Unified command-line entry point.

    arabicmodels info
    arabicmodels tashkeel  {train,eval,infer,tag-data,annotate}
    arabicmodels pos       {train,eval,infer}
    arabicmodels structure {train,eval,infer,spans}

Each group is the same parser exposed by `python -m arabicmodels.<module>`.
"""
import argparse
import sys

from . import __version__


def _info(_args) -> None:
    """Report which checkpoints and datasets are present, with their metrics."""
    from .common import DATA_DIR, MODELS_DIR, device, home, load_ckpt

    print(f"arabicmodels {__version__}")
    print(f"home    : {home()}")
    print(f"device  : {device()}")

    print("\nmodels:")
    expected = [
        ("indexing_wordtagger.pt", "structure segmentation"),
        ("pos_wordtagger.pt", "part-of-speech tagging"),
        ("tashkeel_bilstm.pt", "diacritization v0.1 (characters)"),
        ("tashkeel_bilstm_pos.pt", "diacritization v0.2 (characters + POS)"),
    ]
    for name, desc in expected:
        path = MODELS_DIR / name
        if not path.exists():
            print(f"  [ ] {name:26s} MISSING — run `git lfs pull`")
            continue
        try:
            ck = load_ckpt(path)
        except Exception as e:  # pragma: no cover - diagnostic path
            print(f"  [!] {name:26s} unreadable: {type(e).__name__}: {e}")
            continue
        n = sum(v.numel() for v in ck["state_dict"].values())
        metrics = {k: (round(v, 4) if isinstance(v, float) else v)
                   for k, v in ck.get("metrics", {}).items()
                   if k != "top_confusions"}
        print(f"  [x] {name:26s} v{ck.get('version')}  {n:>9,} params  ({desc})")
        print(f"      dev metrics: {metrics}")

    print("\ndatasets:")
    for name in ("indexing.jsonl", "pos.jsonl", "tashkeel.jsonl",
                 "tashkeel_pos.jsonl"):
        path = DATA_DIR / name
        if path.exists():
            print(f"  [x] {name:22s} {path.stat().st_size / 1024 / 1024:8.1f} MB")
        else:
            print(f"  [ ] {name:22s} MISSING — run `git lfs pull`")


def main(argv: list[str] | None = None) -> None:
    from . import indexing, pos, tashkeel

    ap = argparse.ArgumentParser(
        prog="arabicmodels",
        description="Compact neural models for classical Arabic: "
                    "diacritization, POS tagging, structure segmentation.")
    ap.add_argument("--version", action="version",
                    version=f"arabicmodels {__version__}")
    sub = ap.add_subparsers(dest="group", required=True)

    ip = sub.add_parser("info", help="show installed models, datasets, device")
    ip.set_defaults(func=_info)

    tashkeel.build_parser(
        sub.add_parser("tashkeel", help="diacritization (models B1/B2)"))
    pos.build_parser(
        sub.add_parser("pos", help="part-of-speech tagging (model C)"))
    indexing.build_parser(
        sub.add_parser("structure", help="structure segmentation (model A)"))

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
