"""Import Horizon stories and run the CodeQuest editorial workspace."""

import argparse
from pathlib import Path

import uvicorn

from ..models import ContentItem
from .briefing import build_editorial_packet
from .models import ArticleType
from .preferences import build_preference_profile
from .store import EditorialStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/codequest-editorial.sqlite3")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Import one Horizon ContentItem JSON file")
    import_parser.add_argument("input")

    serve_parser = subparsers.add_parser("serve", help="Run the local editorial workspace")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    preference_parser = subparsers.add_parser(
        "preferences",
        help="Export reusable editor feedback for a future writing assignment",
    )
    preference_parser.add_argument("--article-type", choices=[item.value for item in ArticleType])
    preference_parser.add_argument("--format", choices=("prompt", "json"), default="prompt")

    args = parser.parse_args()
    if args.command == "import":
        item = ContentItem.model_validate_json(Path(args.input).read_text(encoding="utf-8"))
        EditorialStore(args.db).save_packet(build_editorial_packet(item))
        print(f"Imported {item.id} into {args.db}")
        return 0

    if args.command == "preferences":
        profile = build_preference_profile(EditorialStore(args.db), args.article_type)
        if args.format == "json":
            print(profile.model_dump_json(indent=2))
        else:
            print(profile.writer_instructions())
        return 0

    uvicorn.run(
        "src.editorial.web:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
