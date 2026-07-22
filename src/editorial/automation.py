"""Bounded, observable automation for the CodeQuest editorial workspace."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..models import ContentItem
from ..orchestrator import HorizonOrchestrator
from ..storage.manager import StorageManager
from .briefing import build_editorial_packet
from .drafting import ArticleDraftGenerator, create_ollama_cloud_draft_generator
from .models import AutomationRun, AutomationRunStatus
from .preferences import build_preference_profile
from .store import EditorialStore


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class AutomationConfig:
    """Small set of limits controlling automation cost and cadence."""

    schedule_enabled: bool = False
    interval_minutes: int = 1_440
    lookback_hours: int = 48
    max_candidates: int = 5
    auto_select_count: int = 1
    auto_generate: bool = False
    discovery_config_path: Path = Path("data/config.json")

    @classmethod
    def from_env(cls) -> "AutomationConfig":
        max_candidates = _integer("AUTOMATION_MAX_CANDIDATES", 5, 1, 20)
        return cls(
            schedule_enabled=_boolean("AUTOMATION_ENABLED", False),
            interval_minutes=_integer("AUTOMATION_INTERVAL_MINUTES", 1_440, 15, 10_080),
            lookback_hours=_integer("AUTOMATION_LOOKBACK_HOURS", 48, 1, 168),
            max_candidates=max_candidates,
            auto_select_count=_integer(
                "AUTOMATION_AUTO_SELECT_COUNT", 1, 0, max_candidates
            ),
            auto_generate=_boolean("AUTOMATION_AUTO_GENERATE", False),
            discovery_config_path=Path(
                os.getenv("AUTOMATION_DISCOVERY_CONFIG", "data/config.json")
            ),
        )

    @property
    def discovery_ready(self) -> bool:
        return self.discovery_config_path.is_file()


Discover = Callable[[int], Awaitable[list[ContentItem]]]

_CODEQUEST_TERMS = {
    "agent",
    "api",
    "coding",
    "compiler",
    "database",
    "developer",
    "framework",
    "github",
    "ide",
    "library",
    "llm",
    "model",
    "programming",
    "python",
    "sdk",
}
_CODEQUEST_PHRASES = ("artificial intelligence", "developer tool", "open source")


def editorial_relevance(item: ContentItem) -> int:
    """Return a transparent CodeQuest-audience relevance signal."""

    text = " ".join([item.title, *(str(tag) for tag in item.ai_tags)]).lower()
    tokens = set(re.findall(r"[a-z0-9+#.-]+", text))
    matches = sum(term in tokens for term in _CODEQUEST_TERMS)
    matches += sum(phrase in text for phrase in _CODEQUEST_PHRASES)
    return matches


def _priority(item: ContentItem) -> tuple[float, float, datetime]:
    relevance = editorial_relevance(item)
    combined = (item.ai_score or 0) + min(relevance, 4) * 1.5
    return combined, item.ai_score or 0, item.published_at


class EditorialAutomationRunner:
    """Run discovery once and persist every meaningful stage."""

    def __init__(
        self,
        store: EditorialStore,
        config: AutomationConfig,
        discover: Discover,
        draft_generator: ArticleDraftGenerator | None = None,
    ):
        self.store = store
        self.config = config
        self.discover = discover
        self.draft_generator = draft_generator

    async def run_once(self, trigger: str = "manual") -> AutomationRun:
        run = self.store.start_automation_run(trigger)
        draft_errors: list[str] = []
        try:
            run.stage = "discovering"
            self.store.save_automation_run(run)
            candidates = await self.discover(self.config.lookback_hours)
            candidates = sorted(candidates, key=_priority, reverse=True)
            run.discovered_count = len(candidates)

            run.stage = "importing"
            self.store.save_automation_run(run)
            imported: list[tuple[str, int]] = []
            for item in candidates:
                if self.store.get_item(item.id) is not None:
                    run.skipped_count += 1
                    continue
                if len(imported) >= self.config.max_candidates:
                    break
                self.store.save_packet(build_editorial_packet(item))
                imported.append((item.id, editorial_relevance(item)))
                run.imported_count += 1

            selected_ids = [
                content_item_id
                for content_item_id, relevance in imported
                if relevance > 0
            ][: self.config.auto_select_count]
            for content_item_id in selected_ids:
                self.store.set_status(content_item_id, "selected")
                run.selected_count += 1

            if self.config.auto_generate and selected_ids:
                run.stage = "drafting"
                self.store.save_automation_run(run)
                generator = self.draft_generator or create_ollama_cloud_draft_generator()
                for content_item_id in selected_ids:
                    record = self.store.get_item(content_item_id)
                    if record is None:
                        continue
                    try:
                        profile = build_preference_profile(
                            self.store,
                            record.packet.brief.article_type,
                            content_item_id,
                        )
                        draft = await generator.generate(record.packet, profile, [])
                        self.store.save_draft(draft)
                        run.drafted_count += 1
                    except Exception as exc:  # preserve other candidates and record the partial run
                        draft_errors.append(f"{content_item_id}: {str(exc)[:240]}")

            run.finished_at = datetime.now(timezone.utc)
            if draft_errors:
                run.status = AutomationRunStatus.PARTIAL
                run.stage = "needs_attention"
                run.error_message = "; ".join(draft_errors)[:1_000]
            else:
                run.status = AutomationRunStatus.COMPLETED
                run.stage = "complete"
            saved = self.store.save_automation_run(run)
            self.store.checkpoint()
            return saved
        except Exception as exc:
            run.status = AutomationRunStatus.FAILED
            run.stage = "failed"
            run.error_message = str(exc)[:1_000] or exc.__class__.__name__
            run.finished_at = datetime.now(timezone.utc)
            self.store.save_automation_run(run)
            self.store.checkpoint()
            return run
        except asyncio.CancelledError:
            run.status = AutomationRunStatus.FAILED
            run.stage = "interrupted"
            run.error_message = "The automation run was stopped before it finished."
            run.finished_at = datetime.now(timezone.utc)
            self.store.save_automation_run(run)
            self.store.checkpoint()
            raise


def create_automation_runner(
    db_path: str | Path = "data/codequest-editorial.sqlite3",
    config: AutomationConfig | None = None,
) -> EditorialAutomationRunner:
    settings = config or AutomationConfig.from_env()
    storage = StorageManager(data_dir=str(settings.discovery_config_path.parent))
    storage.config_path = settings.discovery_config_path
    horizon_config = storage.load_config()
    orchestrator = HorizonOrchestrator(horizon_config, storage)

    async def discover(hours: int) -> list[ContentItem]:
        return await orchestrator.discover_editorial_candidates(force_hours=hours)

    return EditorialAutomationRunner(
        EditorialStore(db_path),
        settings,
        discover,
    )


async def run_worker(
    runner_factory: Callable[[], EditorialAutomationRunner],
    interval_minutes: int,
) -> None:
    """Run immediately and then at a fixed interval; a separate DB lock prevents overlap."""

    while True:
        await runner_factory().run_once(trigger="schedule")
        await asyncio.sleep(interval_minutes * 60)
