"""Validated dashboard edits for Horizon's native discovery configuration."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..models import Config
from ..storage.manager import ConfigError, StorageManager


_GROUP_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")


def parse_terms(value: str) -> list[str]:
    """Parse comma/newline-separated controls into a stable deduplicated list."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,\n]", value):
        term = raw.strip()
        identity = term.casefold()
        if term and identity not in seen:
            terms.append(term)
            seen.add(identity)
    return terms


class SourceControlService:
    """Apply narrow changes without expanding or exposing environment placeholders."""

    def __init__(self, config_path: str | Path):
        path = Path(config_path)
        self.storage = StorageManager(str(path.parent))
        self.storage.config_path = path

    @property
    def ready(self) -> bool:
        return self.storage.config_path.is_file()

    def load(self) -> Config:
        return self.storage.load_config()

    def load_raw(self) -> dict[str, Any]:
        self.load()
        return self.storage.load_config_data()

    def _save(self, data: dict[str, Any]) -> Config:
        self.storage.save_config_data(data)
        return self.load()

    def update_filtering(
        self,
        *,
        score_threshold: float,
        time_window_hours: int,
        max_items: int | None,
        include_keywords: str,
        exclude_keywords: str,
        default_group_limit: int | None,
    ) -> Config:
        data = deepcopy(self.load_raw())
        filtering = data.setdefault("filtering", {})
        filtering.update(
            {
                "ai_score_threshold": score_threshold,
                "time_window_hours": time_window_hours,
                "max_items": max_items,
                "include_keywords": parse_terms(include_keywords),
                "exclude_keywords": parse_terms(exclude_keywords),
                "default_group_limit": default_group_limit,
            }
        )
        return self._save(data)

    def update_hackernews(
        self, *, enabled: bool, fetch_top_stories: int, min_score: int, category: str
    ) -> Config:
        data = deepcopy(self.load_raw())
        sources = data.setdefault("sources", {})
        sources["hackernews"] = {
            "enabled": enabled,
            "fetch_top_stories": fetch_top_stories,
            "min_score": min_score,
            "category": category.strip() or None,
        }
        return self._save(data)

    def add_rss(self, *, name: str, url: str, category: str, enabled: bool) -> Config:
        data = deepcopy(self.load_raw())
        rss = data.setdefault("sources", {}).setdefault("rss", [])
        normalized_url = url.strip().rstrip("/").casefold()
        if any(
            str(source.get("url", "")).strip().rstrip("/").casefold()
            == normalized_url
            for source in rss
        ):
            raise ValueError("That RSS feed is already configured.")
        rss.append(
            {
                "name": name.strip(),
                "url": url.strip(),
                "enabled": enabled,
                "category": category.strip() or None,
                "content_extractor": "article",
            }
        )
        return self._save(data)

    def update_rss(
        self,
        index: int,
        *,
        name: str,
        url: str,
        category: str,
        enabled: bool,
    ) -> Config:
        data = deepcopy(self.load_raw())
        rss = data.setdefault("sources", {}).setdefault("rss", [])
        if index < 0 or index >= len(rss):
            raise KeyError("RSS source not found.")
        normalized_url = url.strip().rstrip("/").casefold()
        if any(
            position != index
            and str(source.get("url", "")).strip().rstrip("/").casefold()
            == normalized_url
            for position, source in enumerate(rss)
        ):
            raise ValueError("That RSS feed is already configured.")
        existing = rss[index]
        existing.update(
            {
                "name": name.strip(),
                "url": url.strip(),
                "enabled": enabled,
                "category": category.strip() or None,
            }
        )
        return self._save(data)

    def update_category_group(
        self, *, key: str, name: str, categories: str, limit: int
    ) -> Config:
        group_key = key.strip().casefold().replace("_", "-")
        if not _GROUP_KEY.fullmatch(group_key):
            raise ValueError(
                "Group key must use lowercase letters, numbers, and hyphens."
            )
        category_values = parse_terms(categories)
        if not category_values:
            raise ValueError("Add at least one category to the group.")
        data = deepcopy(self.load_raw())
        groups = data.setdefault("filtering", {}).setdefault("category_groups", {})
        groups[group_key] = {
            "name": name.strip() or group_key.replace("-", " ").title(),
            "limit": limit,
            "categories": category_values,
        }
        return self._save(data)


__all__ = ["ConfigError", "SourceControlService", "parse_terms"]
