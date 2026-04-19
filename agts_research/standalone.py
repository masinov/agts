from __future__ import annotations

import argparse
import sys

from agts_research.cli import add_research_subparser, handle_research


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agts-research")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_research_subparser(subparsers)
    args = parser.parse_args(["research", *(sys.argv[1:] if argv is None else argv)])
    return handle_research(args)


if __name__ == "__main__":
    raise SystemExit(main())
