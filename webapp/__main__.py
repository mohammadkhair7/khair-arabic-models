"""Launch the server.

    python -m webapp                    # http://127.0.0.1:8000, localhost only
    python -m webapp --reload           # develop
    python -m webapp --host 0.0.0.0     # expose it (put a TLS proxy in front)

Binding to 127.0.0.1 is the default on purpose. This app parses hostile file
formats, so reaching the internet should be a decision someone makes out loud
rather than something that happens because a default was convenient.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Read `KEY=value` lines from a .env file if one exists.

    Deliberately tiny and deliberately non-overriding: a real environment
    variable always wins, so a production container's injected secret can
    never be clobbered by a stale file someone left in the checkout.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m webapp", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--preload", action="store_true",
                        help="load all models at startup instead of on first use")
    parser.add_argument("--env-file", type=Path,
                        default=Path(__file__).with_name(".env"))
    args = parser.parse_args()

    load_dotenv(args.env_file)
    if args.preload:
        os.environ["ARABICWEB_PRELOAD"] = "1"

    import uvicorn
    print(f"  alarabia.chat  ->  http://{args.host}:{args.port}")
    uvicorn.run("webapp.app.main:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
