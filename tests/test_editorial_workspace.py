from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.editorial.briefing import build_editorial_packet
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
    store.set_status(packet.brief.content_item_id, "approved")

    store.save_packet(packet)

    assert store.get_item(packet.brief.content_item_id).status == "approved"


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

    inbox = client.get("/")
    detail = client.get("/items/rss%3Acodequest%3Aworkspace-1")

    assert inbox.status_code == 200
    assert "Candidate stories" in inbox.text
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


def test_workspace_returns_empty_state_and_missing_item(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    assert "No candidates yet" in client.get("/").text
    assert client.get("/items/missing").status_code == 404
