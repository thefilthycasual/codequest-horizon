import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from src.editorial.buffer import (
    BufferConfig,
    BufferDeliveryRejected,
    BufferDeliveryUncertain,
    BufferPostResult,
    BufferPublisher,
    build_buffer_payload,
    normalize_public_article_url,
    parse_scheduled_time,
)
from src.editorial.models import (
    BufferDeliveryMode,
    BufferDeliveryStatus,
    SocialPlatform,
)
from src.editorial.store import EditorialStore
from src.editorial.web import create_app

from test_editorial_social import _StubSocialGenerator
from test_editorial_workspace import _StubDraftGenerator, _packet


def _config(*, dry_run=False):
    return BufferConfig(
        access_token="test-buffer-token",
        channel_ids={
            SocialPlatform.LINKEDIN: "channel-linkedin",
            SocialPlatform.X: "channel-x",
            SocialPlatform.FACEBOOK: "channel-facebook",
        },
        dry_run=dry_run,
        schedule_timezone="Africa/Johannesburg",
    )


class _SequencePublisher:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create_post(self, payload):
        self.calls.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ready_workspace(db_path, monkeypatch, publisher, *, dry_run=False):
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://www.mycodequest.net")
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    config = _config(dry_run=dry_run)
    client = TestClient(
        create_app(
            db_path,
            draft_generator_factory=_StubDraftGenerator,
            social_generator_factory=_StubSocialGenerator,
            buffer_config_factory=lambda: config,
            buffer_publisher_factory=lambda: publisher,
        )
    )
    item_id = packet.brief.content_item_id
    client.post(f"/items/{item_id}/draft")
    client.post(
        f"/items/{item_id}/decision",
        data={"outcome": "approved", "notes": "Ready for delivery."},
    )
    client.post(f"/items/{item_id}/social")
    article = store.get_latest_draft(item_id)
    wordpress = store.begin_wordpress_delivery(item_id, article.draft_id)
    store.complete_wordpress_delivery(
        wordpress.delivery_id,
        77,
        "https://www.mycodequest.net/?p=77",
        "https://www.mycodequest.net/wp-admin/post.php?post=77&action=edit",
    )
    client.post(
        f"/items/{item_id}/social/public-url",
        data={"public_url": "https://www.mycodequest.net/articles/public-beta/"},
    )
    campaign = store.get_social_campaign(article.draft_id)
    linkedin = next(
        post
        for post in store.list_latest_social_posts(campaign.campaign_id)
        if post.platform == SocialPlatform.LINKEDIN
    )
    client.post(
        f"/items/{item_id}/social/{linkedin.post_id}/decision",
        data={"status": "approved"},
    )
    return client, store, packet, campaign, linkedin


def test_public_url_requires_https_and_the_wordpress_host() -> None:
    assert normalize_public_article_url(
        "https://www.mycodequest.net/article/#section",
        "https://www.mycodequest.net",
    ) == "https://www.mycodequest.net/article/"
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_public_article_url(
            "http://www.mycodequest.net/article/", "https://www.mycodequest.net"
        )
    with pytest.raises(ValueError, match="configured WordPress domain"):
        normalize_public_article_url(
            "https://example.com/article/", "https://www.mycodequest.net"
        )


def test_buffer_payload_uses_current_graphql_scheduling_contract(tmp_path, monkeypatch) -> None:
    publisher = _SequencePublisher([])
    _client, store, packet, campaign, linkedin = _ready_workspace(
        tmp_path / "editorial.sqlite3", monkeypatch, publisher
    )
    scheduled = datetime.now(timezone.utc) + timedelta(hours=3)

    payload = build_buffer_payload(
        linkedin,
        campaign,
        _config(),
        BufferDeliveryMode.CUSTOM_SCHEDULED,
        scheduled,
    )
    post_input = payload["variables"]["input"]

    assert "createPost" in payload["query"]
    assert post_input["channelId"] == "channel-linkedin"
    assert post_input["schedulingType"] == "automatic"
    assert post_input["mode"] == "customScheduled"
    assert post_input["aiAssisted"] is True
    assert post_input["text"].endswith("/articles/public-beta/")
    assert post_input["dueAt"].endswith("Z")
    assert parse_scheduled_time(
        (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        "Africa/Johannesburg",
    ).tzinfo == timezone.utc


def test_dry_run_allows_preview_but_never_starts_delivery(tmp_path, monkeypatch) -> None:
    publisher = _SequencePublisher([])
    client, store, packet, _campaign, linkedin = _ready_workspace(
        tmp_path / "editorial.sqlite3", monkeypatch, publisher, dry_run=True
    )
    item_id = packet.brief.content_item_id

    preview = client.get(
        f"/items/{item_id}/buffer/{linkedin.post_id}/preview?mode=shareNow"
    )
    delivery = client.post(
        f"/items/{item_id}/buffer/{linkedin.post_id}",
        data={"mode": "shareNow", "due_at": ""},
    )

    assert preview.status_code == 200
    assert "BUFFER PAYLOAD PREVIEW" in preview.text
    assert "Buffer delivery disabled" in preview.text
    assert "test-buffer-token" not in preview.text
    assert delivery.status_code == 409
    assert store.get_buffer_delivery(linkedin.post_id) is None
    assert publisher.calls == []


def test_explicit_send_records_success_and_is_idempotent_locally(tmp_path, monkeypatch) -> None:
    publisher = _SequencePublisher(
        [BufferPostResult(post_id="buffer-post-1", status="sent")]
    )
    client, store, packet, _campaign, linkedin = _ready_workspace(
        tmp_path / "editorial.sqlite3", monkeypatch, publisher
    )
    item_id = packet.brief.content_item_id
    request = {"mode": "shareNow", "due_at": ""}

    first = client.post(
        f"/items/{item_id}/buffer/{linkedin.post_id}",
        data=request,
        follow_redirects=False,
    )
    second = client.post(
        f"/items/{item_id}/buffer/{linkedin.post_id}",
        data=request,
        follow_redirects=False,
    )
    delivery = store.get_buffer_delivery(linkedin.post_id)

    assert first.status_code == 303
    assert second.status_code == 303
    assert len(publisher.calls) == 1
    assert delivery.status == BufferDeliveryStatus.DELIVERED
    assert delivery.buffer_post_id == "buffer-post-1"


def test_definitive_failure_can_retry_the_same_intent(tmp_path, monkeypatch) -> None:
    publisher = _SequencePublisher(
        [
            BufferDeliveryRejected("Channel queue is full."),
            BufferPostResult(post_id="buffer-post-2", status="buffer"),
        ]
    )
    client, store, packet, _campaign, linkedin = _ready_workspace(
        tmp_path / "editorial.sqlite3", monkeypatch, publisher
    )
    item_id = packet.brief.content_item_id
    request = {"mode": "shareNow", "due_at": ""}

    failed = client.post(
        f"/items/{item_id}/buffer/{linkedin.post_id}", data=request
    )
    retried = client.post(
        f"/items/{item_id}/buffer/{linkedin.post_id}",
        data=request,
        follow_redirects=False,
    )
    delivery = store.get_buffer_delivery(linkedin.post_id)

    assert failed.status_code == 502
    assert retried.status_code == 303
    assert len(publisher.calls) == 2
    assert delivery.status == BufferDeliveryStatus.DELIVERED
    assert delivery.attempts == 2


def test_uncertain_outcome_blocks_automatic_retry(tmp_path, monkeypatch) -> None:
    publisher = _SequencePublisher(
        [BufferDeliveryUncertain("Check Buffer before retrying.")]
    )
    client, store, packet, _campaign, linkedin = _ready_workspace(
        tmp_path / "editorial.sqlite3", monkeypatch, publisher
    )
    item_id = packet.brief.content_item_id
    request = {"mode": "shareNow", "due_at": ""}

    first = client.post(f"/items/{item_id}/buffer/{linkedin.post_id}", data=request)
    edit = client.post(
        f"/items/{item_id}/social/linkedin/edit",
        data={
            "base_post_id": linkedin.post_id,
            "body": "A revised but potentially duplicate post.",
            "source_ids": "S1",
            "edit_note": "Attempted while uncertain.",
        },
    )
    decision = client.post(
        f"/items/{item_id}/social/{linkedin.post_id}/decision",
        data={"status": "needs_revision"},
    )
    second = client.post(f"/items/{item_id}/buffer/{linkedin.post_id}", data=request)
    delivery = store.get_buffer_delivery(linkedin.post_id)

    assert first.status_code == 502
    assert edit.status_code == 409
    assert decision.status_code == 409
    assert second.status_code == 409
    assert len(publisher.calls) == 1
    assert delivery.status == BufferDeliveryStatus.UNCERTAIN


def test_buffer_publisher_handles_success_rejection_and_ambiguous_response() -> None:
    def response(payload, status=200):
        return httpx.Response(status, json=payload)

    async def run(payload, status=200):
        transport = httpx.MockTransport(lambda _request: response(payload, status))
        async with httpx.AsyncClient(transport=transport) as client:
            return await BufferPublisher(_config(), client).create_post({"query": "mutation"})

    success = asyncio.run(
        run({"data": {"createPost": {"post": {"id": "p1", "status": "buffer"}}}})
    )
    assert success.post_id == "p1"
    with pytest.raises(BufferDeliveryRejected, match="queue is full"):
        asyncio.run(run({"data": {"createPost": {"message": "The queue is full"}}}))
    with pytest.raises(BufferDeliveryUncertain, match="server error"):
        asyncio.run(run({}, status=503))
    with pytest.raises(BufferDeliveryUncertain, match="execution error"):
        asyncio.run(run({"errors": [{"message": "Resolver failed"}]}))
