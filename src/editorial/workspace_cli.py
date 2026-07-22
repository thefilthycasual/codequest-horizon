"""Import Horizon stories and run the CodeQuest editorial workspace."""

import argparse
import asyncio
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from ..models import ContentItem
from .automation import AutomationConfig, create_automation_runner, run_worker
from .briefing import build_editorial_packet
from .drafting import build_draft_prompt, create_ollama_cloud_draft_generator
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

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate and save an unpublished, evidence-grounded article draft",
    )
    generate_parser.add_argument("content_item_id")
    generate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the complete writer prompt without calling Ollama Cloud",
    )

    automation_parser = subparsers.add_parser(
        "automation", help="Run or inspect the bounded editorial automation pipeline"
    )
    automation_commands = automation_parser.add_subparsers(
        dest="automation_command", required=True
    )
    automation_commands.add_parser("run", help="Run discovery once now")
    automation_commands.add_parser(
        "worker", help="Run discovery on the configured opt-in schedule"
    )
    automation_commands.add_parser("status", help="Show recent automation runs")

    args = parser.parse_args()
    load_dotenv()
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

    if args.command == "generate":
        store = EditorialStore(args.db)
        record = store.get_item(args.content_item_id)
        if record is None:
            parser.error(f"Editorial item not found: {args.content_item_id}")
        profile = build_preference_profile(
            store,
            record.packet.brief.article_type,
            args.content_item_id,
        )
        revision_notes = store.latest_revision_notes(args.content_item_id)
        if args.dry_run:
            print(build_draft_prompt(record.packet, profile, revision_notes))
            return 0
        if record.status not in {"selected", "needs_revision"}:
            parser.error("Draft generation requires a Selected or Needs Revision story.")
        generator = create_ollama_cloud_draft_generator()
        draft = asyncio.run(generator.generate(record.packet, profile, revision_notes))
        store.save_draft(draft)
        print(f"Saved unpublished draft {draft.draft_id} for {args.content_item_id}")
        return 0

    if args.command == "automation":
        settings = AutomationConfig.from_env()
        store = EditorialStore(args.db)
        if args.automation_command == "status":
            runs = store.list_automation_runs()
            if not runs:
                print("No automation runs recorded.")
            for run in runs:
                print(
                    f"{run.run_id} {run.status.value} {run.stage} "
                    f"imported={run.imported_count} drafted={run.drafted_count} "
                    f"started={run.started_at.isoformat()}"
                )
            return 0
        if not settings.discovery_ready:
            parser.error(
                f"Discovery configuration not found: {settings.discovery_config_path}"
            )
        if args.automation_command == "worker" and not settings.schedule_enabled:
            parser.error("Set AUTOMATION_ENABLED=true before starting the schedule worker.")
        runner_factory = lambda: create_automation_runner(args.db, settings)
        if args.automation_command == "worker":
            asyncio.run(run_worker(runner_factory, settings.interval_minutes))
            return 0
        run = asyncio.run(runner_factory().run_once(trigger="manual-cli"))
        print(
            f"{run.status.value}: discovered={run.discovered_count} "
            f"imported={run.imported_count} selected={run.selected_count} "
            f"drafted={run.drafted_count}"
        )
        if run.error_message:
            print(run.error_message)
        return 1 if run.status.value == "failed" else 0

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
