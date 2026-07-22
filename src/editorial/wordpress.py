"""WordPress draft rendering and delivery for approved CodeQuest articles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from html import escape
from typing import Any
from urllib.parse import urlparse

import httpx

from .models import ArticleDraft, WordPressCategory


@dataclass(frozen=True)
class WordPressConfig:
    base_url: str
    username: str
    application_password: str
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "WordPressConfig":
        base_url = os.getenv("WORDPRESS_BASE_URL", "").strip().rstrip("/")
        username = os.getenv("WORDPRESS_USERNAME", "").strip()
        application_password = os.getenv("WORDPRESS_APP_PASSWORD", "").strip()
        missing = []
        if not base_url:
            missing.append("WORDPRESS_BASE_URL")
        if not username:
            missing.append("WORDPRESS_USERNAME")
        if not application_password:
            missing.append("WORDPRESS_APP_PASSWORD")
        if missing:
            raise ValueError(
                "WordPress is not configured: missing " + ", ".join(missing) + "."
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("WORDPRESS_BASE_URL must use HTTPS.")
        dry_run = os.getenv("WORDPRESS_DRY_RUN", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        return cls(base_url, username, application_password, dry_run)


@dataclass(frozen=True)
class WordPressDraftResult:
    post_id: int
    post_url: str
    editor_url: str


def render_wordpress_html(draft: ArticleDraft) -> str:
    """Render conservative HTML compatible with WordPress and Elementor HTML blocks."""

    sections: list[str] = [
        f'<p class="codequest-dek"><strong>{escape(draft.dek)}</strong></p>'
    ]
    for section in draft.sections:
        sections.append(f"<h2>{escape(section.heading)}</h2>")
        for paragraph in section.paragraphs:
            citations = " ".join(
                f'<sup><a href="{escape(str(draft.source_map[source_id]), quote=True)}" '
                f'rel="noopener noreferrer">[{escape(source_id)}]</a></sup>'
                for source_id in paragraph.source_ids
            )
            sections.append(f"<p>{escape(paragraph.text)} {citations}</p>")
    sections.append("<h2>Sources</h2><ul>")
    for source_id, source_url in draft.source_map.items():
        safe_id = escape(source_id)
        safe_url = escape(str(source_url), quote=True)
        sections.append(
            f'<li><a href="{safe_url}" rel="noopener noreferrer">{safe_id}: {safe_url}</a></li>'
        )
    sections.append("</ul>")
    return "\n".join(sections)


def build_wordpress_payload(
    draft: ArticleDraft, category_ids: list[int] | None = None
) -> dict[str, Any]:
    """Build an immutable draft-only payload for the WordPress Posts API."""

    payload = {
        "title": draft.title,
        "content": render_wordpress_html(draft),
        "excerpt": draft.dek,
        "status": "draft",
        "slug": _delivery_slug(draft),
    }
    if category_ids:
        payload["categories"] = list(dict.fromkeys(category_ids))
    return payload


def _delivery_slug(draft: ArticleDraft) -> str:
    cleaned = "".join(
        character.lower() if character.isalnum() else "-"
        for character in draft.draft_id
    )
    return "codequest-" + "-".join(part for part in cleaned.split("-") if part)


class WordPressPublisher:
    def __init__(
        self,
        config: WordPressConfig,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self._client = client

    async def list_categories(self) -> list[WordPressCategory]:
        url = f"{self.config.base_url}/wp-json/wp/v2/categories"
        auth = httpx.BasicAuth(self.config.username, self.config.application_password)

        async def fetch(client: httpx.AsyncClient) -> list[WordPressCategory]:
            categories: list[WordPressCategory] = []
            page = 1
            while True:
                response = await client.get(
                    url,
                    auth=auth,
                    params={
                        "context": "edit",
                        "hide_empty": "false",
                        "per_page": 100,
                        "page": page,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("WordPress returned an invalid category response.")
                categories.extend(
                    WordPressCategory(
                        category_id=int(item["id"]),
                        name=str(item["name"]),
                        slug=str(item["slug"]),
                        parent_id=int(item.get("parent") or 0),
                        post_count=int(item.get("count") or 0),
                        description=str(item.get("description") or ""),
                    )
                    for item in payload
                )
                total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
                if page >= total_pages:
                    break
                page += 1
            return categories

        if self._client is not None:
            return await fetch(self._client)
        async with httpx.AsyncClient(timeout=20) as client:
            return await fetch(client)

    async def create_draft(
        self, draft: ArticleDraft, category_ids: list[int] | None = None
    ) -> WordPressDraftResult:
        if self.config.dry_run:
            raise ValueError(
                "WORDPRESS_DRY_RUN is enabled. Preview is available, but delivery is disabled."
            )
        payload = build_wordpress_payload(draft, category_ids)
        if payload.get("status") != "draft":
            raise ValueError("WordPress delivery only supports draft status.")
        url = f"{self.config.base_url}/wp-json/wp/v2/posts"
        auth = httpx.BasicAuth(self.config.username, self.config.application_password)
        if self._client is not None:
            existing = await self._find_existing_draft(url, auth, payload["slug"], self._client)
            if existing:
                return self._result(existing)
            response = await self._client.post(url, auth=auth, json=payload)
        else:
            async with httpx.AsyncClient(timeout=20) as client:
                existing = await self._find_existing_draft(url, auth, payload["slug"], client)
                if existing:
                    return self._result(existing)
                response = await client.post(url, auth=auth, json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "draft":
            raise ValueError("WordPress did not return a draft post.")
        return self._result(data)

    async def _find_existing_draft(
        self,
        url: str,
        auth: httpx.BasicAuth,
        slug: str,
        client: httpx.AsyncClient,
    ) -> dict[str, Any] | None:
        response = await client.get(
            url,
            auth=auth,
            params={"slug": slug, "status": "draft", "context": "edit"},
        )
        response.raise_for_status()
        matches = response.json()
        if not isinstance(matches, list):
            raise ValueError("WordPress returned an invalid draft lookup response.")
        return matches[0] if matches else None

    def _result(self, data: dict[str, Any]) -> WordPressDraftResult:
        if data.get("status") != "draft":
            raise ValueError("WordPress did not return a draft post.")
        post_id = int(data["id"])
        return WordPressDraftResult(
            post_id=post_id,
            post_url=str(data.get("link") or f"{self.config.base_url}/?p={post_id}"),
            editor_url=f"{self.config.base_url}/wp-admin/post.php?post={post_id}&action=edit",
        )
