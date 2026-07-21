"""Create a CodeQuest editorial packet from a serialized Horizon item."""

import argparse
import sys
from pathlib import Path

from ..models import ContentItem
from .briefing import build_editorial_packet


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="ContentItem JSON file, or - for stdin")
    parser.add_argument("--output", help="Write packet JSON to this file instead of stdout")
    args = parser.parse_args()

    item = ContentItem.model_validate_json(_read_input(args.input))
    payload = build_editorial_packet(item).model_dump_json(indent=2)

    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
