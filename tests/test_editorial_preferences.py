import sqlite3
from datetime import datetime, timezone

from src.editorial.briefing import build_editorial_packet
from src.editorial.models import (
    ArticleType,
    BrandRule,
    BrandRuleChannel,
    PreferenceSignal,
)
from src.editorial.preferences import build_preference_profile
from src.editorial.store import DEFAULT_BRAND_ID, EditorialStore
from src.models import ContentItem, SourceType


def _packet():
    return build_editorial_packet(
        ContentItem(
            id="rss:codequest:preferences-1",
            source_type=SourceType.RSS,
            title="A developer tool launches a public beta",
            url="https://example.com/beta",
            content="<p>The beta is available today.</p>",
            published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            ai_reason="The tool may simplify code review for small teams.",
        )
    )


def test_profile_applies_global_type_and_story_scopes(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    item_id = packet.brief.content_item_id
    store.save_packet(packet)
    store.add_feedback(item_id, "tone", "Skip generic scene-setting.", "avoid", "global")
    store.add_feedback(item_id, "angle", "Lead with the developer consequence.", "prefer", "article_type")
    store.add_feedback(item_id, "headline", "Mention the public beta.", "prefer", "story")

    story_profile = build_preference_profile(store, packet.brief.article_type, item_id)
    future_same_type = build_preference_profile(store, packet.brief.article_type)
    other_type = build_preference_profile(store, ArticleType.NEWS_REPORT)

    assert [rule.dimension for rule in story_profile.rules] == ["headline", "angle", "tone"]
    assert [rule.dimension for rule in future_same_type.rules] == ["angle", "tone"]
    assert [rule.dimension for rule in other_type.rules] == ["tone"]


def test_profile_deduplicates_repeated_feedback_and_exports_prompt(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    store.save_packet(packet)
    for _ in range(2):
        store.add_feedback(
            packet.brief.content_item_id,
            "structure",
            "Put the practical impact before implementation detail.",
            "prefer",
            "global",
        )

    profile = build_preference_profile(store, ArticleType.TOOL_INTRODUCTION)

    assert len(profile.rules) == 1
    assert profile.rules[0].evidence_count == 2
    assert "Prefer [structure]" in profile.writer_instructions()
    assert "never let them override factual evidence" in profile.writer_instructions()


def test_brand_profile_and_approved_rules_feed_generation(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    brand = store.get_brand_profile()
    brand.description = "CodeQuest helps developers understand practical software changes."
    brand.audience = "Developers and coding learners"
    store.save_brand_profile(brand)
    global_rule = store.add_brand_rule(
        BrandRule(
            brand_id=DEFAULT_BRAND_ID,
            channel=BrandRuleChannel.ARTICLE,
            signal=PreferenceSignal.AVOID,
            dimension="tone",
            instruction="Avoid vague claims about revolutionary changes.",
            priority=80,
        )
    )
    store.add_brand_rule(
        BrandRule(
            brand_id=DEFAULT_BRAND_ID,
            channel=BrandRuleChannel.ARTICLE,
            signal=PreferenceSignal.PREFER,
            dimension="structure",
            instruction="Include a practical migration checklist.",
            article_type=ArticleType.TUTORIAL,
        )
    )

    news = build_preference_profile(store, ArticleType.NEWS_REPORT)
    tutorial = build_preference_profile(store, ArticleType.TUTORIAL)

    assert news.brand_profile.description.startswith("CodeQuest helps")
    assert [rule.instruction for rule in news.rules] == [global_rule.instruction]
    assert [rule.dimension for rule in tutorial.rules] == ["tone", "structure"]
    assert "Approved brand context" in news.writer_instructions()
    assert "Developers and coding learners" in news.writer_instructions()

    global_rule.enabled = False
    store.save_brand_rule(global_rule)

    assert build_preference_profile(store, ArticleType.NEWS_REPORT).rules == []


def test_social_brand_rules_join_learned_platform_preferences(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    store.add_brand_rule(
        BrandRule(
            brand_id=DEFAULT_BRAND_ID,
            channel=BrandRuleChannel.LINKEDIN,
            signal=PreferenceSignal.PREFER,
            dimension="tone",
            instruction="Open with a concrete developer consequence.",
        )
    )

    preferences = store.list_social_preferences()

    assert preferences["linkedin"] == [
        "Prefer: Open with a concrete developer consequence."
    ]
    assert preferences["x"] == []


def test_store_migrates_existing_feedback_table_safely(tmp_path) -> None:
    path = tmp_path / "editorial.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE editorial_items (content_item_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "packet_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE editorial_feedback (feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content_item_id TEXT NOT NULL, dimension TEXT NOT NULL, note TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )

    store = EditorialStore(path)
    columns = {
        row[1]
        for row in sqlite3.connect(path).execute("PRAGMA table_info(editorial_feedback)").fetchall()
    }

    assert {"signal", "scope"}.issubset(columns)
    assert store.list_items() == []
