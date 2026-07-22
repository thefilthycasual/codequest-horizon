"""Build a conservative first editorial brief from a Horizon content item."""

import re
from html import unescape
from typing import Any

from ..models import ContentItem
from .models import (
    ArticleType,
    DiscoveryInsight,
    EditorialBrief,
    EditorialPacket,
    EvidencePack,
    EvidenceSource,
)


_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _plain_text(value: str | None, limit: int = 2_000) -> str:
    if not value:
        return ""
    text = unescape(_HTML_TAG.sub(" ", value))
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:limit].rstrip()


def _article_type(item: ContentItem) -> ArticleType:
    category = str(item.metadata.get("category") or "")
    tags = " ".join(str(tag) for tag in item.ai_tags)
    text = f"{item.title} {category} {tags}".lower()

    if any(term in text for term in ("tutorial", "how to", "step-by-step", "guide")):
        return ArticleType.TUTORIAL
    if any(term in text for term in ("opinion", "analysis", "debate", "controversy")):
        return ArticleType.ANALYSIS
    if any(term in text for term in ("launch", "released", "release", "new tool", "open source")):
        return ArticleType.TOOL_INTRODUCTION
    if any(term in text for term in ("explainer", "what is", "why does", "understanding")):
        return ArticleType.EXPLAINER
    return ArticleType.NEWS_REPORT


def _supporting_sources(item: ContentItem, primary_url: str) -> list[EvidenceSource]:
    sources: list[EvidenceSource] = []
    seen = {primary_url.rstrip("/")}
    raw_sources = item.metadata.get("sources", [])
    if not isinstance(raw_sources, list):
        return sources

    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        normalized = url.rstrip("/")
        if not url or normalized in seen:
            continue
        seen.add(normalized)
        try:
            sources.append(
                EvidenceSource(
                    url=url,
                    title=str(raw.get("title") or url),
                    publisher=_optional_text(raw.get("publisher")),
                    excerpt=_plain_text(_optional_text(raw.get("excerpt")), limit=1_000),
                    is_primary=False,
                )
            )
        except ValueError:
            continue
    return sources


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _discovery_insight(item: ContentItem) -> DiscoveryInsight:
    metadata = item.metadata
    engagement: dict[str, int | float | str] = {}
    for key in (
        "score",
        "descendants",
        "favorite_count",
        "retweet_count",
        "reply_count",
        "views",
        "bookmarks",
        "upvote_ratio",
    ):
        value = metadata.get(key)
        if isinstance(value, (int, float, str)) and str(value).strip():
            engagement[key] = value
    tags = list(dict.fromkeys(str(tag).strip() for tag in item.ai_tags if str(tag).strip()))
    return DiscoveryInsight(
        horizon_item_id=item.id,
        source_type=item.source_type,
        author=item.author or "",
        published_at=item.published_at,
        fetched_at=item.fetched_at,
        ai_score=item.ai_score,
        ai_reason=_plain_text(item.ai_reason, 2_000),
        ai_summary=_plain_text(item.ai_summary, 4_000),
        ai_tags=tags,
        category=_plain_text(_optional_text(metadata.get("category")), 200),
        engagement=engagement,
        discussion_url=_plain_text(_optional_text(metadata.get("discussion_url")), 2_000),
        detailed_summary=_plain_text(
            _optional_text(metadata.get("detailed_summary_en"))
            or _optional_text(metadata.get("detailed_summary")),
            12_000,
        ),
        background=_plain_text(
            _optional_text(metadata.get("background_en"))
            or _optional_text(metadata.get("background")),
            12_000,
        ),
        community_discussion=_plain_text(
            _optional_text(metadata.get("community_discussion_en"))
            or _optional_text(metadata.get("community_discussion")),
            12_000,
        ),
    )


def build_editorial_packet(item: ContentItem) -> EditorialPacket:
    """Convert one filtered Horizon item into an editable CodeQuest handoff.

    This function intentionally does not invent research. Horizon's original
    URL and extracted article text form the primary evidence; enrichment URLs
    are carried as supporting leads. Missing evidence becomes an explicit
    unresolved question for the editor or a later research stage.
    """

    primary_url = str(item.url)
    primary_excerpt = _plain_text(item.content)
    publisher = _optional_text(item.metadata.get("feed_name")) or item.author
    sources = [
        EvidenceSource(
            url=item.url,
            title=item.title,
            publisher=publisher,
            source_type=item.source_type,
            excerpt=primary_excerpt,
            is_primary=True,
        )
    ]
    sources.extend(_supporting_sources(item, primary_url))

    unresolved: list[str] = []
    if not primary_excerpt:
        unresolved.append("Fetch and review the full primary source before drafting.")
    if len(sources) == 1:
        unresolved.append("Find at least one independent or primary corroborating source.")

    angle = _optional_text(item.ai_reason) or _optional_text(item.ai_summary) or item.title
    required_facts = [item.title]
    if item.ai_summary and item.ai_summary.strip() != item.title.strip():
        required_facts.append(item.ai_summary.strip())

    evidence = EvidencePack(
        content_item_id=item.id,
        sources=sources,
        unresolved_questions=unresolved,
    )
    brief = EditorialBrief(
        content_item_id=item.id,
        working_title=item.title,
        article_type=_article_type(item),
        central_angle=angle,
        audience_value=(
            "Explain the concrete consequence for developers, coding learners, "
            "or people choosing development tools."
        ),
        required_facts=required_facts,
        uncertainty_notes=list(unresolved),
    )
    return EditorialPacket(brief=brief, evidence=evidence, discovery=_discovery_insight(item))
