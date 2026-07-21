from datetime import timedelta

import pytest

from src.editorial.models import (
    ArticleDraft,
    DecisionOutcome,
    DraftDecision,
    DraftParagraph,
    DraftSection,
    QualityCheckStatus,
)
from src.editorial.quality import evaluate_draft
from src.editorial.store import EditorialStore

from test_editorial_workspace import _packet


def _draft(packet, *, source_ids=None, title="A grounded review draft") -> ArticleDraft:
    return ArticleDraft(
        content_item_id=packet.brief.content_item_id,
        title=title,
        dek="A public beta focuses on developer code-review workflows.",
        sections=[
            DraftSection(
                heading="What changed",
                paragraphs=[
                    DraftParagraph(
                        text="The public beta is available for code review workflows.",
                        source_ids=source_ids or ["S1", "S2"],
                    )
                ],
            )
        ],
        source_map={
            "S1": packet.evidence.sources[0].url,
            "S2": packet.evidence.sources[1].url,
        },
        generator_model="test-writer",
    )


def test_quality_report_blocks_unknown_citations() -> None:
    packet = _packet()
    report = evaluate_draft(packet, _draft(packet, source_ids=["S1", "S99"]))

    unknown_check = next(check for check in report.checks if check.key == "known_sources")

    assert unknown_check.status == QualityCheckStatus.BLOCK
    assert report.can_approve is False


def test_persisted_review_queues_latest_draft_for_discord(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    draft = _draft(packet)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    store.save_draft(draft)
    decision = DraftDecision(
        content_item_id=packet.brief.content_item_id,
        draft_id=draft.draft_id,
        outcome=DecisionOutcome.READY_FOR_APPROVAL,
        notes="Ready for Discord approval.",
        quality_report=evaluate_draft(packet, draft),
    )

    store.record_decision(decision)

    assert store.get_item(packet.brief.content_item_id).status == "ready_for_approval"
    assert store.get_latest_decision(packet.brief.content_item_id).decision_id == decision.decision_id
    with pytest.raises(ValueError, match="persisted editorial decision"):
        store.set_status(packet.brief.content_item_id, "approved")


def test_revision_decision_requires_notes_and_feeds_next_draft(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    draft = _draft(packet)
    store.save_packet(packet)
    store.save_draft(draft)
    report = evaluate_draft(packet, draft)

    with pytest.raises(ValueError, match="require editor notes"):
        store.record_decision(
            DraftDecision(
                content_item_id=packet.brief.content_item_id,
                draft_id=draft.draft_id,
                outcome=DecisionOutcome.NEEDS_REVISION,
                quality_report=report,
            )
        )

    decision = DraftDecision(
        content_item_id=packet.brief.content_item_id,
        draft_id=draft.draft_id,
        outcome=DecisionOutcome.NEEDS_REVISION,
        notes="Explain the limitation of the small independent test.",
        quality_report=report,
    )
    store.record_decision(decision)

    assert store.get_item(packet.brief.content_item_id).status == "needs_revision"
    assert store.latest_revision_notes(packet.brief.content_item_id) == [decision.notes]


def test_only_latest_draft_can_receive_decision(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    first = _draft(packet, title="First draft")
    second = _draft(packet, title="Second draft")
    second.created_at = first.created_at + timedelta(seconds=1)
    store.save_packet(packet)
    store.save_draft(first)
    store.save_draft(second)

    with pytest.raises(ValueError, match="latest draft"):
        store.record_decision(
            DraftDecision(
                content_item_id=packet.brief.content_item_id,
                draft_id=first.draft_id,
                outcome=DecisionOutcome.READY_FOR_APPROVAL,
                quality_report=evaluate_draft(packet, first),
            )
        )
