"""Compile explicit editor feedback into transparent writing constraints."""

from __future__ import annotations

from collections import OrderedDict

from .models import (
    ArticleType,
    BrandRuleChannel,
    EditorialPreferenceProfile,
    PreferenceRule,
)
from .store import EditorialStore


def build_preference_profile(
    store: EditorialStore,
    article_type: ArticleType | str | None = None,
    content_item_id: str | None = None,
    brand_id: str | None = None,
) -> EditorialPreferenceProfile:
    """Build a deduplicated profile while retaining evidence of repeated feedback."""

    parsed_type = ArticleType(article_type) if article_type else None
    feedback = store.list_applicable_feedback(
        article_type=parsed_type.value if parsed_type else None,
        content_item_id=content_item_id,
        brand_id=brand_id,
    )
    approved_rules = [
        rule
        for rule in store.list_brand_rules(
            brand_id=brand_id,
            channel=BrandRuleChannel.ARTICLE,
            enabled=True,
        )
        if rule.article_type is None or rule.article_type == parsed_type
    ]
    feedback.extend(
        {
            "signal": rule.signal.value,
            "dimension": rule.dimension,
            "note": rule.instruction,
            "scope": "article_type" if rule.article_type else "global",
            "created_at": rule.updated_at.isoformat(),
        }
        for rule in approved_rules
    )
    grouped: OrderedDict[tuple[str, str, str, str], PreferenceRule] = OrderedDict()
    for entry in feedback:
        instruction = str(entry["note"]).strip()
        key = (
            str(entry["signal"]),
            str(entry["dimension"]),
            instruction.casefold(),
            str(entry["scope"]),
        )
        if key in grouped:
            grouped[key].evidence_count += 1
            continue
        grouped[key] = PreferenceRule(
            signal=str(entry["signal"]),
            dimension=str(entry["dimension"]),
            instruction=instruction,
            scope=str(entry["scope"]),
            latest_feedback_at=str(entry["created_at"]),
        )
    return EditorialPreferenceProfile(
        article_type=parsed_type,
        content_item_id=content_item_id,
        brand_profile=store.get_brand_profile(brand_id),
        rules=list(grouped.values()),
    )
