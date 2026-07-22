from datetime import datetime, timezone
import json
from pathlib import Path

from src.editorial.briefing import build_editorial_packet
from src.editorial.models import ArticleType
from src.models import Config, ContentItem, SourceType


def _item(**overrides) -> ContentItem:
    values = {
        "id": "rss:codequest:story-1",
        "source_type": SourceType.RSS,
        "title": "Example launches an open source coding assistant",
        "url": "https://example.com/launch",
        "content": "<p>The assistant is available today.</p>",
        "author": "Example",
        "published_at": datetime(2026, 7, 21, tzinfo=timezone.utc),
        "ai_summary": "Example released a coding assistant for developers.",
        "ai_reason": "The release may change how junior developers review code.",
        "ai_score": 8.7,
        "ai_tags": ["developer-tools", "open-source"],
        "metadata": {
            "feed_name": "Example Engineering",
            "category": "developer-tools",
            "views": 4200,
            "reply_count": 18,
            "discussion_url": "https://community.example/thread/1",
            "detailed_summary_en": "The release adds repository-aware review workflows.",
            "background_en": "The company previously offered a private preview.",
            "community_discussion_en": "Developers are discussing accuracy and pricing.",
            "sources": [
                {
                    "url": "https://independent.example/review",
                    "title": "Independent hands-on review",
                }
            ],
        },
    }
    values.update(overrides)
    return ContentItem(**values)


def test_builds_typed_editorial_handoff_from_horizon_item() -> None:
    packet = build_editorial_packet(_item())

    assert packet.brief.article_type == ArticleType.TOOL_INTRODUCTION
    assert packet.brief.central_angle.startswith("The release may change")
    assert packet.evidence.primary_source.publisher == "Example Engineering"
    assert packet.evidence.primary_source.excerpt == "The assistant is available today."
    assert len(packet.evidence.supporting_sources) == 1
    assert packet.evidence.unresolved_questions == []
    assert packet.discovery is not None
    assert packet.discovery.ai_score == 8.7
    assert packet.discovery.ai_tags == ["developer-tools", "open-source"]
    assert packet.discovery.engagement == {"reply_count": 18, "views": 4200}
    assert packet.discovery.detailed_summary.startswith("The release adds")
    assert packet.discovery.background.startswith("The company previously")
    assert packet.discovery.community_discussion.startswith("Developers are discussing")


def test_missing_body_and_corroboration_are_visible_not_invented() -> None:
    item = _item(content=None, metadata={"category": "news"})
    packet = build_editorial_packet(item)

    assert packet.evidence.primary_source.excerpt == ""
    assert len(packet.evidence.unresolved_questions) == 2
    assert packet.brief.uncertainty_notes == packet.evidence.unresolved_questions


def test_invalid_and_duplicate_supporting_urls_are_ignored() -> None:
    item = _item(
        metadata={
            "sources": [
                {"url": "https://example.com/launch/", "title": "Duplicate"},
                {"url": "not-a-url", "title": "Invalid"},
                {"url": "https://valid.example/context", "title": "Context"},
            ]
        }
    )
    packet = build_editorial_packet(item)

    assert [str(source.url) for source in packet.evidence.sources] == [
        "https://example.com/launch",
        "https://valid.example/context",
    ]


def test_article_type_is_not_forced_to_tutorial() -> None:
    news = build_editorial_packet(_item(title="Company reports quarterly platform update"))
    tutorial = build_editorial_packet(_item(title="How to test a coding assistant safely"))

    assert news.brief.article_type == ArticleType.NEWS_REPORT
    assert tutorial.brief.article_type == ArticleType.TUTORIAL


def test_codequest_example_configuration_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "data" / "config.codequest.example.json").read_text())

    config = Config.model_validate(payload)

    assert config.ai.provider.value == "ollama"
    assert config.ai.base_url == "${OLLAMA_BASE_URL}"
    assert [source.name for source in config.sources.rss] == [
        "OpenAI News",
        "GitHub Blog",
        "Google Developers Blog",
    ]
    assert config.extractors["article"].favor_precision is True
