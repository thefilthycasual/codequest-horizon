import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.editorial.automation import AutomationConfig, EditorialAutomationRunner
from src.editorial.models import (
    ArticleDraft,
    AutomationRunStatus,
    DraftParagraph,
    DraftSection,
)
from src.editorial.store import EditorialStore
from src.editorial.web import create_app
from src.models import ContentItem, SourceType


def _item(number: int, score: float) -> ContentItem:
    return ContentItem(
        id=f"rss:automation:{number}",
        source_type=SourceType.RSS,
        title=f"Automation story {number}",
        url=f"https://example.com/story-{number}",
        content=f"Verified source material for story {number}.",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=number),
        ai_score=score,
        ai_reason="Useful developer tooling update.",
        ai_tags=["developer", "tooling"],
    )


class _DraftGenerator:
    async def generate(self, packet, _profile, _revision_notes):
        return ArticleDraft(
            content_item_id=packet.brief.content_item_id,
            title=packet.brief.working_title,
            dek="A grounded automation draft.",
            sections=[
                DraftSection(
                    heading="What changed",
                    paragraphs=[
                        DraftParagraph(text="The source describes the update.", source_ids=["S1"])
                    ],
                )
            ],
            source_map={"S1": packet.evidence.primary_source.url},
            generator_model="test-model",
        )


def test_automation_is_bounded_selects_top_story_and_records_duplicates(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")

    async def discover(_hours: int):
        return [_item(1, 7), _item(2, 9), _item(3, 8)]

    runner = EditorialAutomationRunner(
        store,
        AutomationConfig(max_candidates=2, auto_select_count=1),
        discover,
    )
    first = asyncio.run(runner.run_once())
    assert first.status == AutomationRunStatus.COMPLETED
    assert first.discovered_count == 3
    assert first.imported_count == 2
    assert first.selected_count == 1
    assert first.imported_item_ids == ["rss:automation:2", "rss:automation:3"]
    assert first.selected_item_ids == ["rss:automation:2"]
    assert store.get_item("rss:automation:2").status == "selected"
    assert store.get_item("rss:automation:3").status == "candidate"

    second = asyncio.run(runner.run_once())
    assert second.imported_count == 1
    assert second.skipped_count == 2
    assert len(store.list_automation_runs()) == 2

    third = asyncio.run(runner.run_once())
    assert third.imported_count == 0
    assert third.skipped_count == 3


def test_automation_does_not_auto_select_an_off_topic_high_score(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    off_topic = _item(1, 10)
    off_topic.title = "A major development in pure mathematics"
    off_topic.ai_tags = ["mathematics", "research"]
    relevant = _item(2, 7)
    relevant.title = "New open source Python developer tool"

    async def discover(_hours: int):
        return [off_topic, relevant]

    run = asyncio.run(
        EditorialAutomationRunner(
            store,
            AutomationConfig(max_candidates=2, auto_select_count=1),
            discover,
        ).run_once()
    )
    assert run.selected_count == 1
    assert store.get_item(relevant.id).status == "selected"
    assert store.get_item(off_topic.id).status == "candidate"


def test_automation_can_prepare_unapproved_draft_when_explicitly_enabled(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")

    async def discover(_hours: int):
        return [_item(1, 9)]

    runner = EditorialAutomationRunner(
        store,
        AutomationConfig(max_candidates=1, auto_select_count=1, auto_generate=True),
        discover,
        draft_generator=_DraftGenerator(),
    )
    run = asyncio.run(runner.run_once())
    assert run.drafted_count == 1
    assert store.get_item("rss:automation:1").status == "selected"
    assert store.get_latest_draft("rss:automation:1") is not None
    assert store.get_latest_decision("rss:automation:1") is None


def test_automation_failure_is_durable_and_overlap_is_blocked(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")

    async def broken(_hours: int):
        raise RuntimeError("source unavailable")

    failed = asyncio.run(
        EditorialAutomationRunner(store, AutomationConfig(), broken).run_once()
    )
    assert failed.status == AutomationRunStatus.FAILED
    assert store.get_automation_run(failed.run_id).error_message == "source unavailable"

    store.start_automation_run("worker-a")
    with pytest.raises(ValueError, match="already in progress"):
        store.start_automation_run("worker-b")


def test_cancelled_automation_is_recorded_as_interrupted(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def discover(_hours: int):
            started.set()
            await release.wait()
            return []

        task = asyncio.create_task(
            EditorialAutomationRunner(store, AutomationConfig(), discover).run_once()
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    run = store.list_automation_runs()[0]
    assert run.status == AutomationRunStatus.FAILED
    assert run.stage == "interrupted"


def test_operations_page_runs_injected_pipeline(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")

    async def discover(_hours: int):
        return [_item(1, 9)]

    runner = EditorialAutomationRunner(
        store,
        AutomationConfig(max_candidates=1, auto_select_count=0),
        discover,
    )
    app = create_app(
        tmp_path / "editorial.sqlite3",
        automation_runner_factory=lambda: runner,
    )
    client = TestClient(app)

    response = client.get("/operations")
    assert response.status_code == 200
    assert "Run discovery now" in response.text
    assert "Human approval is always required" in response.text

    response = client.post("/operations/run", follow_redirects=True)
    assert response.status_code == 200
    assert "1 discovered" in response.text
    assert "1 imported" in response.text
    assert "Manual workspace run" in response.text
    assert "Automation story 1" in response.text
    assert "Open editorial queue" in response.text
