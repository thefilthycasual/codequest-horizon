import asyncio

import httpx
from fastapi.testclient import TestClient

from src.editorial.models import (
    ArticleDraft,
    DecisionOutcome,
    DraftDecision,
    DraftParagraph,
    DraftSection,
    WordPressCategory,
)
from src.editorial.quality import evaluate_draft
from src.editorial.store import EditorialStore
from src.editorial.web import create_app
from src.editorial.wordpress import (
    WordPressConfig,
    WordPressDraftResult,
    WordPressPublisher,
    build_wordpress_payload,
)

from test_editorial_workspace import _packet


def _draft(packet) -> ArticleDraft:
    return ArticleDraft(
        content_item_id=packet.brief.content_item_id,
        title="A safe <launch> story",
        dek="What developers should know & verify.",
        sections=[
            DraftSection(
                heading="What <changed>",
                paragraphs=[
                    DraftParagraph(
                        text="The beta blocks <script>alert('no')</script> unsupported output.",
                        source_ids=["S1"],
                    )
                ],
            )
        ],
        source_map={"S1": packet.evidence.sources[0].url},
        generator_model="test-writer",
    )


def _approved_store(path):
    store = EditorialStore(path)
    packet = _packet()
    draft = _draft(packet)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    store.save_draft(draft)
    store.record_decision(
        DraftDecision(
            content_item_id=packet.brief.content_item_id,
            draft_id=draft.draft_id,
            outcome=DecisionOutcome.APPROVED,
            notes="Approved in the workspace.",
            quality_report=evaluate_draft(packet, draft),
        )
    )
    return store, packet, draft


class _StubPublisher:
    def __init__(self):
        self.calls = 0
        self.category_ids = []

    async def list_categories(self):
        return [
            WordPressCategory(
                category_id=7,
                name="Developer Tools",
                slug="developer-tools",
                post_count=12,
            ),
            WordPressCategory(
                category_id=9,
                name="AI",
                slug="ai",
                post_count=20,
            ),
        ]

    async def create_draft(self, draft, category_ids=None):
        self.calls += 1
        self.category_ids = category_ids or []
        return WordPressDraftResult(
            post_id=42,
            post_url="https://wordpress.example/?p=42",
            editor_url="https://wordpress.example/wp-admin/post.php?post=42&action=edit",
        )


class _FlakyPublisher(_StubPublisher):
    async def create_draft(self, draft, category_ids=None):
        self.calls += 1
        self.category_ids = category_ids or []
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        return WordPressDraftResult(
            post_id=43,
            post_url="https://wordpress.example/?p=43",
            editor_url="https://wordpress.example/wp-admin/post.php?post=43&action=edit",
        )


def test_wordpress_payload_is_draft_only_and_escapes_generated_html() -> None:
    packet = _packet()
    payload = build_wordpress_payload(_draft(packet))

    assert payload["status"] == "draft"
    assert set(payload) == {"title", "content", "excerpt", "status", "slug"}
    assert payload["slug"].startswith("codequest-draft-")
    assert "<script>" not in payload["content"]
    assert "&lt;script&gt;" in payload["content"]
    assert "<h2>What &lt;changed&gt;</h2>" in payload["content"]
    assert "https://example.com/beta" in payload["content"]
    assert build_wordpress_payload(_draft(packet), [7, 9, 7])["categories"] == [7, 9]


def test_wordpress_publisher_loads_paginated_categories() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        payload = [
            {
                "id": page,
                "name": f"Category {page}",
                "slug": f"category-{page}",
                "parent": 0,
                "count": page * 2,
                "description": "",
            }
        ]
        return httpx.Response(200, json=payload, headers={"X-WP-TotalPages": "2"})

    async def load():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = WordPressPublisher(
                WordPressConfig(
                    "https://wp.example",
                    "editor",
                    "application-password",
                    dry_run=True,
                ),
                client,
            )
            return await publisher.list_categories()

    categories = asyncio.run(load())

    assert [category.category_id for category in categories] == [1, 2]
    assert all(request.url.path.endswith("/wp-json/wp/v2/categories") for request in requests)
    assert requests[0].url.params["hide_empty"] == "false"


def test_wordpress_publisher_uses_posts_api_basic_auth_and_requires_draft() -> None:
    packet = _packet()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={"id": 91, "status": "draft", "link": "https://wp.example/?p=91"},
        )

    async def publish():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = WordPressPublisher(
                WordPressConfig(
                    "https://wp.example",
                    "editor",
                    "application-password",
                    dry_run=False,
                ),
                client,
            )
            return await publisher.create_draft(_draft(packet))

    result = asyncio.run(publish())

    assert captured["url"] == "https://wp.example/wp-json/wp/v2/posts"
    assert captured["authorization"].startswith("Basic ")
    assert b'"status":"draft"' in captured["body"]
    assert result.post_id == 91
    assert result.editor_url.endswith("post=91&action=edit")


def test_wordpress_publisher_recovers_an_existing_draft_after_ambiguous_retry() -> None:
    packet = _packet()
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(
            200,
            json=[{"id": 92, "status": "draft", "link": "https://wp.example/?p=92"}],
        )

    async def publish():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            publisher = WordPressPublisher(
                WordPressConfig(
                    "https://wp.example",
                    "editor",
                    "application-password",
                    dry_run=False,
                ),
                client,
            )
            return await publisher.create_draft(_draft(packet))

    result = asyncio.run(publish())

    assert methods == ["GET"]
    assert result.post_id == 92


def test_approved_draft_preview_and_delivery_are_idempotent(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    store, packet, draft = _approved_store(db_path)
    publisher = _StubPublisher()
    client = TestClient(
        create_app(db_path, wordpress_publisher_factory=lambda: publisher)
    )
    item_id = packet.brief.content_item_id

    preview = client.get(f"/items/{item_id}/wordpress/preview")
    first = client.post(f"/items/{item_id}/wordpress", follow_redirects=False)
    second = client.post(f"/items/{item_id}/wordpress", follow_redirects=False)
    detail = client.get(f"/items/{item_id}?tab=delivery")
    delivery = store.get_wordpress_delivery(draft.draft_id)

    assert preview.status_code == 200
    assert "WORDPRESS PAYLOAD PREVIEW" in preview.text
    assert "status <strong>draft</strong>" in preview.text
    assert first.status_code == 303
    assert second.status_code == 303
    assert publisher.calls == 1
    assert delivery.post_id == 42
    assert delivery.attempts == 1
    assert "Open in WordPress editor" in detail.text


def test_publishing_hub_syncs_and_assigns_real_wordpress_categories(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    store, packet, _draft_record = _approved_store(db_path)
    publisher = _StubPublisher()
    client = TestClient(
        create_app(db_path, wordpress_publisher_factory=lambda: publisher)
    )
    item_id = packet.brief.content_item_id

    synced = client.post(
        "/publishing/wordpress/categories/sync", follow_redirects=False
    )
    assigned = client.post(
        f"/items/{item_id}/wordpress/settings",
        data={"category_id": ["7", "9"]},
        follow_redirects=False,
    )
    hub = client.get("/publishing")
    delivery_page = client.get(
        f"/items/{item_id}?tab=delivery&channel=wordpress"
    )
    preview = client.get(f"/items/{item_id}/wordpress/preview")
    delivered = client.post(f"/items/{item_id}/wordpress", follow_redirects=False)

    assert synced.status_code == 303
    assert assigned.status_code == 303
    assert "Developer Tools" in hub.text
    assert "Where should this article appear?" in delivery_page.text
    assert "Developer Tools, AI" in preview.text
    assert delivered.status_code == 303
    assert publisher.category_ids == [7, 9]
    assert store.get_wordpress_publishing_settings(item_id).category_ids == [7, 9]


def test_wordpress_delivery_requires_exact_workspace_approval(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    store.save_draft(_draft(packet))
    client = TestClient(create_app(db_path))

    preview = client.get(f"/items/{packet.brief.content_item_id}/wordpress/preview")
    delivery = client.post(f"/items/{packet.brief.content_item_id}/wordpress")

    assert preview.status_code == 409
    assert delivery.status_code == 409


def test_failed_wordpress_delivery_is_retryable_without_a_duplicate_record(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    store, packet, draft = _approved_store(db_path)
    publisher = _FlakyPublisher()
    client = TestClient(
        create_app(db_path, wordpress_publisher_factory=lambda: publisher)
    )
    item_id = packet.brief.content_item_id

    failed = client.post(f"/items/{item_id}/wordpress", follow_redirects=False)
    failed_record = store.get_wordpress_delivery(draft.draft_id)
    retried = client.post(f"/items/{item_id}/wordpress", follow_redirects=False)
    completed = store.get_wordpress_delivery(draft.draft_id)

    assert failed.status_code == 502
    assert failed_record.status.value == "failed"
    assert retried.status_code == 303
    assert publisher.calls == 2
    assert completed.status.value == "draft_created"
    assert completed.attempts == 2
    assert completed.delivery_id == failed_record.delivery_id
