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
    WordPressMediaItem,
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
        self.featured_media_id = None
        self.uploaded = None

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

    async def list_media(self):
        return [
            WordPressMediaItem(
                media_id=30,
                title="Orange code illustration",
                filename="orange-code.webp",
                source_url="https://wordpress.example/uploads/orange-code.webp",
                thumbnail_url="https://wordpress.example/uploads/orange-code-300.webp",
                mime_type="image/webp",
                alt_text="Code editor with an orange interface",
                width=1200,
                height=675,
            )
        ]

    async def upload_media(
        self, *, filename, content_type, content, title, alt_text
    ):
        self.uploaded = (filename, content_type, content, title, alt_text)
        return WordPressMediaItem(
            media_id=31,
            title=title,
            filename=filename,
            source_url="https://wordpress.example/uploads/new-image.png",
            thumbnail_url="https://wordpress.example/uploads/new-image-300.png",
            mime_type=content_type,
            alt_text=alt_text,
            width=1200,
            height=675,
        )

    async def create_draft(
        self, draft, category_ids=None, featured_media_id=None
    ):
        self.calls += 1
        self.category_ids = category_ids or []
        self.featured_media_id = featured_media_id
        return WordPressDraftResult(
            post_id=42,
            post_url="https://wordpress.example/?p=42",
            editor_url="https://wordpress.example/wp-admin/post.php?post=42&action=edit",
        )


class _FlakyPublisher(_StubPublisher):
    async def create_draft(
        self, draft, category_ids=None, featured_media_id=None
    ):
        self.calls += 1
        self.category_ids = category_ids or []
        self.featured_media_id = featured_media_id
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
    featured_payload = build_wordpress_payload(_draft(packet), [7], 30)
    assert featured_payload["featured_media"] == 30


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


def test_wordpress_publisher_loads_image_media_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 30,
                    "date_gmt": "2026-07-22T10:00:00",
                    "slug": "orange-code",
                    "title": {"rendered": "Orange code illustration"},
                    "caption": {"rendered": "A generated illustration"},
                    "alt_text": "Code editor with orange accents",
                    "mime_type": "image/webp",
                    "source_url": "https://wp.example/orange-code.webp",
                    "media_details": {
                        "file": "2026/07/orange-code.webp",
                        "width": 1200,
                        "height": 675,
                        "sizes": {
                            "medium": {
                                "source_url": "https://wp.example/orange-code-300.webp"
                            }
                        },
                    },
                }
            ],
        )

    async def load():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WordPressPublisher(
                WordPressConfig(
                    "https://wp.example",
                    "editor",
                    "application-password",
                    dry_run=True,
                ),
                client,
            ).list_media()

    media = asyncio.run(load())

    assert media[0].media_id == 30
    assert media[0].filename == "orange-code.webp"
    assert media[0].thumbnail_url.endswith("orange-code-300.webp")
    assert media[0].width == 1200


def test_wordpress_publisher_uploads_image_with_accessible_metadata() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={
                "id": 31,
                "slug": "new-dashboard-image",
                "title": {"rendered": "New dashboard image"},
                "caption": {"rendered": ""},
                "alt_text": "An orange editorial dashboard",
                "mime_type": "image/png",
                "source_url": "https://wp.example/new-dashboard-image.png",
                "media_details": {"file": "new-dashboard-image.png"},
            },
        )

    async def upload():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WordPressPublisher(
                WordPressConfig(
                    "https://wp.example",
                    "editor",
                    "application-password",
                    dry_run=False,
                ),
                client,
            ).upload_media(
                filename="new-dashboard-image.png",
                content_type="image/png",
                content=b"\x89PNG\r\n\x1a\nsafe-image-bytes",
                title="New dashboard image",
                alt_text="An orange editorial dashboard",
            )

    item = asyncio.run(upload())

    assert captured["method"] == "POST"
    assert captured["content_type"].startswith("multipart/form-data;")
    assert b"safe-image-bytes" in captured["body"]
    assert b"An orange editorial dashboard" in captured["body"]
    assert item.media_id == 31


def test_media_upload_is_guarded_and_cached(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    publisher = _StubPublisher()
    client = TestClient(
        create_app(db_path, wordpress_publisher_factory=lambda: publisher)
    )

    rejected = client.post(
        "/publishing/wordpress/media/upload",
        files={"image": ("unsafe.svg", b"<svg></svg>", "image/svg+xml")},
        data={"title": "Unsafe", "alt_text": "Unsafe vector"},
    )
    uploaded = client.post(
        "/publishing/wordpress/media/upload",
        files={
            "image": (
                "new image.png",
                b"\x89PNG\r\n\x1a\nsafe-image-bytes",
                "image/png",
            )
        },
        data={
            "title": "New dashboard image",
            "alt_text": "An orange editorial dashboard",
        },
        follow_redirects=False,
    )

    assert rejected.status_code == 400
    assert uploaded.status_code == 303
    assert publisher.uploaded[0] == "newimage.png"
    assert publisher.uploaded[1] == "image/png"
    cached = EditorialStore(db_path).list_wordpress_media()
    assert cached[0].media_id == 31
    assert cached[0].alt_text == "An orange editorial dashboard"


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
    media_synced = client.post(
        "/publishing/wordpress/media/sync", follow_redirects=False
    )
    assigned = client.post(
        f"/items/{item_id}/wordpress/settings",
        data={"category_id": ["7", "9"], "featured_media_id": "30"},
        follow_redirects=False,
    )
    hub = client.get("/publishing")
    media_page = client.get("/publishing?tab=media&q=orange")
    delivery_page = client.get(
        f"/items/{item_id}?tab=delivery&channel=wordpress"
    )
    preview = client.get(f"/items/{item_id}/wordpress/preview")
    delivered = client.post(f"/items/{item_id}/wordpress", follow_redirects=False)

    assert synced.status_code == 303
    assert media_synced.status_code == 303
    assert assigned.status_code == 303
    assert "Developer Tools" in hub.text
    assert "Orange code illustration" in media_page.text
    assert "1 matching" in media_page.text
    assert "Where should this article appear?" in delivery_page.text
    assert "Developer Tools, AI" in preview.text
    assert "Orange code illustration" in preview.text
    assert delivered.status_code == 303
    assert publisher.category_ids == [7, 9]
    assert publisher.featured_media_id == 30
    assert store.get_wordpress_publishing_settings(item_id).category_ids == [7, 9]
    assert store.get_wordpress_publishing_settings(item_id).featured_media_id == 30


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
