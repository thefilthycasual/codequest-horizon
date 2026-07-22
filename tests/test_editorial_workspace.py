from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from src.editorial.briefing import build_editorial_packet
from src.editorial.models import ArticleDraft, DraftParagraph, DraftSection
from src.editorial.store import EditorialStore
from src.editorial.web import create_app
from src.models import ContentItem, SourceType


def _packet():
    item = ContentItem(
        id="rss:codequest:workspace-1",
        source_type=SourceType.RSS,
        title="A developer tool launches a public beta",
        url="https://example.com/beta",
        content="<p>The beta is available today.</p>",
        author="Example Engineering",
        published_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        ai_reason="The tool may simplify code review for small teams.",
        ai_summary="A public beta is now available.",
        metadata={
            "feed_name": "Example Engineering",
            "sources": [
                {
                    "url": "https://review.example/beta",
                    "title": "Independent review",
                }
            ],
        },
    )
    return build_editorial_packet(item)


class _StubDraftGenerator:
    async def generate(self, packet, profile, revision_notes=None):
        return ArticleDraft(
            content_item_id=packet.brief.content_item_id,
            title="A <script>grounded</script> review draft",
            dek="A concise, evidence-linked summary.",
            sections=[
                DraftSection(
                    heading="What changed",
                    paragraphs=[
                        DraftParagraph(
                            text="The public beta is available today.",
                            source_ids=["S1"],
                        )
                    ],
                )
            ],
            source_map={"S1": packet.evidence.sources[0].url},
            preference_rules=profile.rules,
            revision_notes=revision_notes or [],
            generator_model="test-writer",
        )


class _StubDiscordBridge:
    sent = []
    closed = []

    async def send_request(self, request, draft, quality):
        self.sent.append((request.request_id, draft.draft_id, quality.can_approve))
        return "channel-1", "message-1"

    async def close_request_message(self, request, summary):
        self.closed.append((request.request_id, summary))


def _signed_discord_post(client, signing_key, payload):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = "1784600000"
    signature = signing_key.sign(timestamp.encode() + body).signature.hex()
    return client.post(
        "/discord/interactions",
        content=body,
        headers={
            "content-type": "application/json",
            "x-signature-ed25519": signature,
            "x-signature-timestamp": timestamp,
        },
    )


def test_editorial_store_round_trip_and_feedback(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()

    store.save_packet(packet)
    record = store.get_item(packet.brief.content_item_id)

    assert record is not None
    assert record.status == "candidate"
    assert record.packet.brief.working_title == packet.brief.working_title

    store.set_status(packet.brief.content_item_id, "selected")
    store.add_feedback(packet.brief.content_item_id, "angle", "Lead with the code-review use case.")

    assert store.get_item(packet.brief.content_item_id).status == "selected"
    assert store.list_feedback(packet.brief.content_item_id)[0]["note"].startswith("Lead with")


def test_store_preserves_status_when_refreshing_packet(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")

    store.save_packet(packet)

    assert store.get_item(packet.brief.content_item_id).status == "selected"


def test_store_rejects_unknown_status_and_empty_feedback(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    store.save_packet(packet)

    with pytest.raises(ValueError, match="Unsupported editorial status"):
        store.set_status(packet.brief.content_item_id, "published")
    with pytest.raises(ValueError, match="must not be empty"):
        store.add_feedback(packet.brief.content_item_id, "general", "  ")
    with pytest.raises(ValueError, match="Unsupported feedback signal"):
        store.add_feedback(packet.brief.content_item_id, "tone", "No hype.", signal="maybe")
    with pytest.raises(ValueError, match="Unsupported feedback scope"):
        store.add_feedback(packet.brief.content_item_id, "tone", "No hype.", scope="account")


def test_workspace_renders_inbox_detail_and_escaped_content(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    packet.brief.working_title = "A <script>alert('no')</script> story"
    EditorialStore(db_path).save_packet(packet)
    client = TestClient(create_app(db_path))

    overview = client.get("/")
    inbox = client.get("/editorial")
    detail = client.get("/items/rss%3Acodequest%3Aworkspace-1")

    assert overview.status_code == 200
    assert "WORKSPACE OVERVIEW" in overview.text
    assert "Editorial queue" in overview.text
    assert inbox.status_code == 200
    assert "Find the signal" in inbox.text
    assert "&lt;script&gt;" in inbox.text
    assert "<script>alert" not in inbox.text
    assert detail.status_code == 200
    assert "Central angle" in detail.text
    assert "Independent review" in detail.text


def test_workspace_records_status_and_feedback(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    EditorialStore(db_path).save_packet(packet)
    client = TestClient(create_app(db_path))
    item_id = packet.brief.content_item_id

    status_response = client.post(
        f"/items/{item_id}/status",
        data={"status": "needs_revision"},
        follow_redirects=False,
    )
    feedback_response = client.post(
        f"/items/{item_id}/feedback",
        data={
            "signal": "avoid",
            "dimension": "tone",
            "scope": "global",
            "note": "Do not open with generic scene-setting.",
        },
        follow_redirects=False,
    )

    store = EditorialStore(db_path)
    assert status_response.status_code == 303
    assert feedback_response.status_code == 303
    assert store.get_item(item_id).status == "needs_revision"
    assert store.list_feedback(item_id)[0]["dimension"] == "tone"
    assert store.list_feedback(item_id)[0]["signal"] == "avoid"
    assert "Do not open with generic" in client.get("/preferences").text


def test_editorial_queue_supports_filter_search_and_quick_selection(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    EditorialStore(db_path).save_packet(packet)
    client = TestClient(create_app(db_path))
    item_id = packet.brief.content_item_id

    queue = client.get("/editorial?status=candidate&q=developer")

    assert queue.status_code == 200
    assert "Candidates 1" in queue.text
    assert "Select story" in queue.text
    assert "Search stories" in queue.text

    selected = client.post(
        f"/items/{item_id}/status",
        data={"status": "selected", "return_to": "editorial"},
        follow_redirects=False,
    )

    assert selected.status_code == 303
    assert selected.headers["location"] == "/editorial?status=selected"
    assert "Open workspace" in client.get(selected.headers["location"]).text
    assert "No matching stories" in client.get("/editorial?q=not-a-real-story").text


def test_workspace_returns_empty_state_and_missing_item(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    assert "No candidates" in client.get("/editorial").text
    assert client.get("/items/missing").status_code == 404


def test_workspace_generates_and_renders_unpublished_draft(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))

    response = client.post(
        f"/items/{packet.brief.content_item_id}/draft",
        follow_redirects=False,
    )
    detail = client.get(f"/items/{packet.brief.content_item_id}?tab=editor")

    assert response.status_code == 303
    assert store.get_latest_draft(packet.brief.content_item_id) is not None
    assert store.get_item(packet.brief.content_item_id).status == "selected"
    assert "UNPUBLISHED REVIEW DRAFT" in detail.text
    assert "test-writer" in detail.text
    assert "&lt;script&gt;grounded" in detail.text
    assert "<script>grounded" not in detail.text


def test_workspace_lists_and_confirms_required_facts(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))
    item_path = f"/items/{packet.brief.content_item_id}"

    client.post(f"{item_path}/draft")
    draft = store.get_latest_draft(packet.brief.content_item_id)
    review = client.get(f"{item_path}?tab=review")

    assert "Required fact review" in review.text
    assert "A developer tool launches a public beta" in review.text
    assert "S1:" in review.text
    assert "Confirm selected facts" in review.text

    response = client.post(
        f"{item_path}/facts/confirm",
        data={"draft_id": draft.draft_id, "fact_index": "0"},
        follow_redirects=False,
    )
    completed_review = client.get(f"{item_path}?tab=review")

    assert response.status_code == 303
    assert store.list_confirmed_required_facts(draft.draft_id) == {
        "A developer tool launches a public beta"
    }
    assert "Every required fact is represented" in completed_review.text
    assert "explicitly confirmed" in completed_review.text
    assert "does not approve the article" in completed_review.text


def test_story_workspace_tabs_keep_each_stage_focused(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))
    client.post(f"/items/{packet.brief.content_item_id}/draft")
    path = f"/items/{packet.brief.content_item_id}"

    overview = client.get(path)
    editor = client.get(f"{path}?tab=editor")
    review = client.get(f"{path}?tab=review")
    delivery = client.get(f"{path}?tab=delivery")
    learning = client.get(f"{path}?tab=learning")

    assert "Editorial queue" in overview.text
    assert "Story workspace" in overview.text
    assert "Central angle" in overview.text
    assert "UNPUBLISHED REVIEW DRAFT" not in overview.text
    assert "UNPUBLISHED REVIEW DRAFT" in editor.text
    assert "Version history" in editor.text
    assert "Quality gate" not in editor.text
    assert "Quality gate" in review.text
    assert "WordPress · locked" in delivery.text
    assert "Discord · optional" in delivery.text
    assert "Effective writing profile" in learning.text
    assert "Add feedback" in learning.text
    assert client.get(f"{path}?tab=missing").status_code == 404


def test_workspace_blocks_draft_generation_for_candidate(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    EditorialStore(db_path).save_packet(packet)
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))

    response = client.post(f"/items/{packet.brief.content_item_id}/draft")

    assert response.status_code == 409


def test_workspace_queues_reviewed_draft_for_discord_approval(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))
    client.post(f"/items/{packet.brief.content_item_id}/draft")

    response = client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "ready_for_approval", "notes": "Ready for Discord."},
        follow_redirects=False,
    )
    review = client.get(f"/items/{packet.brief.content_item_id}?tab=review")
    delivery = client.get(f"/items/{packet.brief.content_item_id}?tab=delivery")

    assert response.status_code == 303
    assert store.get_item(packet.brief.content_item_id).status == "ready_for_approval"
    assert store.get_latest_decision(packet.brief.content_item_id).notes.startswith("Ready")
    assert "workspace editor can now make the final decision" in review.text
    assert "Share with Discord" in delivery.text


def test_workspace_rejects_status_only_approval(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    EditorialStore(db_path).save_packet(packet)
    client = TestClient(create_app(db_path))

    response = client.post(
        f"/items/{packet.brief.content_item_id}/status",
        data={"status": "approved"},
    )

    assert response.status_code == 400


def test_discord_signed_endorsement_is_advisory_only(tmp_path, monkeypatch) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_APPROVAL_CHANNEL_ID", "channel-1")
    monkeypatch.setenv(
        "DISCORD_APPLICATION_PUBLIC_KEY", signing_key.verify_key.encode().hex()
    )
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(
        create_app(
            db_path,
            draft_generator_factory=_StubDraftGenerator,
            discord_bridge_factory=_StubDiscordBridge,
        )
    )
    client.post(f"/items/{packet.brief.content_item_id}/draft")
    client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "ready_for_approval", "notes": "Reviewed."},
    )

    delivery = client.post(
        f"/items/{packet.brief.content_item_id}/discord", follow_redirects=False
    )
    request = store.get_latest_discord_request(packet.brief.content_item_id)
    response = _signed_discord_post(
        client,
        signing_key,
        {
            "type": 3,
            "data": {"custom_id": f"cq:approve:{request.request_id}"},
            "member": {"user": {"id": "editor-1", "username": "Herman"}},
            "message": {"embeds": [{"title": "Reviewed draft"}]},
        },
    )

    assert delivery.status_code == 303
    assert request.message_id == "message-1"
    assert response.status_code == 200
    assert response.json()["type"] == 7
    assert store.get_item(packet.brief.content_item_id).status == "ready_for_approval"
    assert store.get_latest_discord_request(packet.brief.content_item_id).status.value == "endorsed"
    assert store.get_latest_discord_request(packet.brief.content_item_id).resolved_by_name == "Herman"


def test_discord_revision_suggestions_do_not_change_editorial_state(tmp_path, monkeypatch) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_APPROVAL_CHANNEL_ID", "channel-1")
    monkeypatch.setenv(
        "DISCORD_APPLICATION_PUBLIC_KEY", signing_key.verify_key.encode().hex()
    )
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(
        create_app(
            db_path,
            draft_generator_factory=_StubDraftGenerator,
            discord_bridge_factory=_StubDiscordBridge,
        )
    )
    client.post(f"/items/{packet.brief.content_item_id}/draft")
    client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "ready_for_approval", "notes": "Reviewed."},
    )
    client.post(f"/items/{packet.brief.content_item_id}/discord")
    request = store.get_latest_discord_request(packet.brief.content_item_id)

    modal = _signed_discord_post(
        client,
        signing_key,
        {
            "type": 5,
            "data": {
                "custom_id": f"cq:revision_modal:{request.request_id}",
                "components": [
                    {
                        "type": 1,
                        "components": [
                            {
                                "type": 4,
                                "custom_id": "revision_notes",
                                "value": "Make the opening more specific.",
                            }
                        ],
                    }
                ],
            },
            "member": {"user": {"id": "editor-2", "username": "Editor"}},
        },
    )

    assert modal.status_code == 200
    assert store.get_item(packet.brief.content_item_id).status == "ready_for_approval"
    assert store.latest_revision_notes(packet.brief.content_item_id) == []
    request = store.get_latest_discord_request(packet.brief.content_item_id)
    assert request.status.value == "revision_suggested"
    assert request.revision_notes == "Make the opening more specific."


def test_workspace_editor_can_make_final_approval(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))
    client.post(f"/items/{packet.brief.content_item_id}/draft")

    response = client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "approved", "notes": "Final approval in workspace."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert store.get_item(packet.brief.content_item_id).status == "approved"
    assert store.get_latest_decision(packet.brief.content_item_id).outcome.value == "approved"


def test_discord_rejects_unsigned_interactions(tmp_path, monkeypatch) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_APPROVAL_CHANNEL_ID", "channel-1")
    monkeypatch.setenv(
        "DISCORD_APPLICATION_PUBLIC_KEY", signing_key.verify_key.encode().hex()
    )
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    response = client.post("/discord/interactions", json={"type": 1})

    assert response.status_code == 401
