from fastapi.testclient import TestClient

from src.editorial.store import EditorialStore
from src.editorial.web import create_app

from test_editorial_workspace import _StubDraftGenerator, _packet


def _workspace_with_draft(db_path):
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(create_app(db_path, draft_generator_factory=_StubDraftGenerator))
    client.post(f"/items/{packet.brief.content_item_id}/draft")
    return client, store, packet


def _edit_payload(draft, **overrides):
    payload = {
        "base_draft_id": draft.draft_id,
        "title": "A clearer human-edited headline",
        "dek": "A more specific summary for developers.",
        "section_heading": ["What changed in practice"],
        "paragraph_section": ["0"],
        "paragraph_text": ["The public beta is available for code review today."],
        "paragraph_sources": ["S1"],
        "edit_note": "Made the practical impact more specific.",
    }
    payload.update(overrides)
    return payload


def test_editor_saves_a_new_version_without_overwriting_the_original(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    client, store, packet = _workspace_with_draft(db_path)
    original = store.get_latest_draft(packet.brief.content_item_id)

    editor = client.get(f"/items/{packet.brief.content_item_id}/edit")
    response = client.post(
        f"/items/{packet.brief.content_item_id}/edit",
        data=_edit_payload(original),
        follow_redirects=False,
    )
    versions = store.list_drafts(packet.brief.content_item_id)
    latest = versions[0]
    detail = client.get(f"/items/{packet.brief.content_item_id}")

    assert editor.status_code == 200
    assert "Save as new version" in editor.text
    assert "&lt;script&gt;grounded&lt;/script&gt;" in editor.text
    assert "<script>grounded</script>" not in editor.text
    assert response.status_code == 303
    assert len(versions) == 2
    assert latest.draft_id != original.draft_id
    assert latest.parent_draft_id == original.draft_id
    assert latest.edit_note.startswith("Made the practical")
    assert latest.title == "A clearer human-edited headline"
    assert versions[1].title == original.title
    assert store.get_item(packet.brief.content_item_id).status == "selected"
    assert "requires a fresh editorial decision" in detail.text
    assert "2 draft version(s)" in detail.text


def test_editing_an_approved_draft_invalidates_approval_for_the_new_version(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    client, store, packet = _workspace_with_draft(db_path)
    original = store.get_latest_draft(packet.brief.content_item_id)
    client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "approved", "notes": "Approved before edit."},
    )

    response = client.post(
        f"/items/{packet.brief.content_item_id}/edit",
        data=_edit_payload(original),
        follow_redirects=False,
    )
    latest = store.get_latest_draft(packet.brief.content_item_id)
    preview = client.get(
        f"/items/{packet.brief.content_item_id}/wordpress/preview"
    )

    assert response.status_code == 303
    assert store.get_item(packet.brief.content_item_id).status == "selected"
    assert store.get_latest_decision(packet.brief.content_item_id).draft_id == original.draft_id
    assert latest.draft_id != original.draft_id
    assert preview.status_code == 409


def test_editor_rejects_stale_versions_and_unknown_evidence(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    client, store, packet = _workspace_with_draft(db_path)
    original = store.get_latest_draft(packet.brief.content_item_id)

    unknown = client.post(
        f"/items/{packet.brief.content_item_id}/edit",
        data=_edit_payload(original, paragraph_sources=["S99"]),
    )
    stale = client.post(
        f"/items/{packet.brief.content_item_id}/edit",
        data=_edit_payload(original, base_draft_id="draft_stale"),
    )

    assert unknown.status_code == 400
    assert "Unknown evidence IDs" in unknown.text
    assert stale.status_code == 409
    assert len(store.list_drafts(packet.brief.content_item_id)) == 1


def test_editor_is_locked_after_a_wordpress_draft_is_created(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    client, store, packet = _workspace_with_draft(db_path)
    draft = store.get_latest_draft(packet.brief.content_item_id)
    client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "approved", "notes": "Ready for WordPress."},
    )
    delivery = store.begin_wordpress_delivery(packet.brief.content_item_id, draft.draft_id)
    store.complete_wordpress_delivery(
        delivery.delivery_id,
        88,
        "https://wordpress.example/?p=88",
        "https://wordpress.example/wp-admin/post.php?post=88&action=edit",
    )

    editor = client.get(f"/items/{packet.brief.content_item_id}/edit")
    detail = client.get(f"/items/{packet.brief.content_item_id}")

    assert editor.status_code == 409
    assert "already has a WordPress draft" in editor.text
    assert "Edit this version" not in detail.text
    assert "Open in WordPress editor" in detail.text
