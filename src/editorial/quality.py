"""Deterministic quality gates applied before human draft approval."""

from __future__ import annotations

import re
from collections.abc import Collection

from .models import (
    ArticleDraft,
    DraftQualityCheck,
    DraftQualityReport,
    EditorialPacket,
    QualityCheckStatus,
)


def pending_required_facts(
    packet: EditorialPacket,
    draft: ArticleDraft,
    confirmed_facts: Collection[str] = (),
) -> list[str]:
    """Return facts that are neither textually represented nor human-confirmed."""

    paragraphs = [paragraph for section in draft.sections for paragraph in section.paragraphs]
    draft_text = " ".join(
        [draft.title, draft.dek] + [paragraph.text for paragraph in paragraphs]
    )
    draft_words = _meaningful_words(draft_text)
    confirmed = set(confirmed_facts)
    return [
        fact
        for fact in packet.brief.required_facts
        if fact not in confirmed
        and _meaningful_words(fact)
        and not _meaningful_words(fact).issubset(draft_words)
    ]


def evaluate_draft(
    packet: EditorialPacket,
    draft: ArticleDraft,
    confirmed_facts: Collection[str] = (),
) -> DraftQualityReport:
    checks: list[DraftQualityCheck] = []
    paragraphs = [paragraph for section in draft.sections for paragraph in section.paragraphs]
    used_sources = {source_id for paragraph in paragraphs for source_id in paragraph.source_ids}
    known_sources = set(draft.source_map)

    checks.append(
        DraftQualityCheck(
            key="story_match",
            label="Correct assignment",
            status=(
                QualityCheckStatus.PASS
                if draft.content_item_id == packet.brief.content_item_id
                else QualityCheckStatus.BLOCK
            ),
            detail=(
                "Draft is attached to the selected editorial brief."
                if draft.content_item_id == packet.brief.content_item_id
                else "Draft belongs to a different editorial brief."
            ),
        )
    )
    uncited = sum(not paragraph.source_ids for paragraph in paragraphs)
    checks.append(
        DraftQualityCheck(
            key="paragraph_citations",
            label="Paragraph citations",
            status=QualityCheckStatus.PASS if uncited == 0 else QualityCheckStatus.BLOCK,
            detail=(
                "Every paragraph names its supporting evidence."
                if uncited == 0
                else f"{uncited} paragraph(s) have no evidence citation."
            ),
        )
    )
    unknown = used_sources - known_sources
    checks.append(
        DraftQualityCheck(
            key="known_sources",
            label="Known evidence only",
            status=QualityCheckStatus.PASS if not unknown else QualityCheckStatus.BLOCK,
            detail=(
                "All citations resolve to the saved evidence packet."
                if not unknown
                else f"Unknown evidence IDs: {', '.join(sorted(unknown))}."
            ),
        )
    )
    checks.append(
        DraftQualityCheck(
            key="primary_source",
            label="Primary source used",
            status=QualityCheckStatus.PASS if "S1" in used_sources else QualityCheckStatus.BLOCK,
            detail=(
                "The draft cites the primary source."
                if "S1" in used_sources
                else "The draft never cites the primary source."
            ),
        )
    )
    supporting_used = any(source_id != "S1" for source_id in used_sources)
    has_supporting = len(packet.evidence.sources) > 1
    checks.append(
        DraftQualityCheck(
            key="source_diversity",
            label="Independent support",
            status=(
                QualityCheckStatus.PASS
                if supporting_used
                else QualityCheckStatus.WARNING
            ),
            detail=(
                "The draft uses supporting evidence beyond the primary source."
                if supporting_used
                else (
                    "Supporting evidence exists but is not cited in the draft."
                    if has_supporting
                    else "No supporting source is available yet."
                )
            ),
        )
    )
    checks.append(
        DraftQualityCheck(
            key="research_gaps",
            label="Research gaps",
            status=(
                QualityCheckStatus.WARNING
                if packet.evidence.unresolved_questions
                else QualityCheckStatus.PASS
            ),
            detail=(
                "; ".join(packet.evidence.unresolved_questions)
                if packet.evidence.unresolved_questions
                else "No unresolved evidence questions are recorded."
            ),
        )
    )

    draft_text = " ".join(
        [draft.title, draft.dek]
        + [paragraph.text for paragraph in paragraphs]
    ).casefold()
    missing_facts = pending_required_facts(packet, draft, confirmed_facts)
    checks.append(
        DraftQualityCheck(
            key="required_facts",
            label="Required facts",
            status=QualityCheckStatus.WARNING if missing_facts else QualityCheckStatus.PASS,
            detail=(
                "Review these required facts: " + " | ".join(missing_facts)
                if missing_facts
                else "All required facts are represented in the draft or explicitly confirmed."
            ),
        )
    )
    word_count = len(re.findall(r"\b\w+\b", draft_text))
    if draft.prompt_version == "codequest-draft-v2":
        depth_status = (
            QualityCheckStatus.PASS
            if word_count >= 350
            else QualityCheckStatus.WARNING
            if word_count >= 180
            else QualityCheckStatus.BLOCK
        )
    else:
        depth_status = (
            QualityCheckStatus.PASS if word_count >= 250 else QualityCheckStatus.WARNING
        )
    checks.append(
        DraftQualityCheck(
            key="draft_depth",
            label="Draft depth",
            status=depth_status,
            detail=f"Draft contains approximately {word_count} words.",
        )
    )
    return DraftQualityReport(draft_id=draft.draft_id, checks=checks)


def _meaningful_words(value: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", value.casefold())
        if len(word) > 3
    }
