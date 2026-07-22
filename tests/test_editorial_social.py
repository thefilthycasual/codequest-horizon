import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from src.editorial.models import (
    SocialCampaign,
    SocialPlatform,
    SocialPostDraft,
    SocialPostStatus,
)
from src.editorial.social import SocialCampaignGenerator, SocialGenerationError
from src.editorial.store import EditorialStore
from src.editorial.web import create_app

from test_editorial_workspace import _StubDraftGenerator, _packet


class _StubSocialGenerator:
    async def generate(self, content_item_id, draft, preferences=None):
        campaign = SocialCampaign(
            content_item_id=content_item_id,
            article_draft_id=draft.draft_id,
            generator_model="test-social-writer",
            preference_snapshot=preferences or {},
        )
        bodies = {
            SocialPlatform.LINKEDIN: "The public beta is now available for developers reviewing code.",
            SocialPlatform.X: "A new code-review beta is available today.",
            SocialPlatform.FACEBOOK: "Developers can now try the public beta for code review.",
        }
        posts = [
            SocialPostDraft(
                campaign_id=campaign.campaign_id,
                content_item_id=content_item_id,
                article_draft_id=draft.draft_id,
                platform=platform,
                body=body,
                source_ids=["S1"],
                generator_model="test-social-writer",
            )
            for platform, body in bodies.items()
        ]
        return campaign, posts


class _FakeClient:
    def __init__(self, response):
        self.response = response

    async def complete(self, **_kwargs):
        return json.dumps(self.response)


def _approved_workspace(db_path):
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(
        create_app(
            db_path,
            draft_generator_factory=_StubDraftGenerator,
            social_generator_factory=_StubSocialGenerator,
        )
    )
    client.post(f"/items/{packet.brief.content_item_id}/draft")
    client.post(
        f"/items/{packet.brief.content_item_id}/decision",
        data={"outcome": "approved", "notes": "Ready for campaign copy."},
    )
    return client, store, packet


def test_social_generation_is_locked_to_the_latest_approved_article(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    packet = _packet()
    store = EditorialStore(db_path)
    store.save_packet(packet)
    store.set_status(packet.brief.content_item_id, "selected")
    client = TestClient(
        create_app(
            db_path,
            draft_generator_factory=_StubDraftGenerator,
            social_generator_factory=_StubSocialGenerator,
        )
    )
    client.post(f"/items/{packet.brief.content_item_id}/draft")

    locked = client.get(
        f"/items/{packet.brief.content_item_id}?tab=delivery&channel=social"
    )
    response = client.post(f"/items/{packet.brief.content_item_id}/social")

    assert "Social · locked" in locked.text
    assert response.status_code == 409
    assert store.get_social_campaign(store.get_latest_draft(packet.brief.content_item_id).draft_id) is None


def test_workspace_generates_a_focused_three_platform_campaign(tmp_path) -> None:
    client, store, packet = _approved_workspace(tmp_path / "editorial.sqlite3")
    item_id = packet.brief.content_item_id

    response = client.post(f"/items/{item_id}/social", follow_redirects=False)
    draft = store.get_latest_draft(item_id)
    campaign = store.get_social_campaign(draft.draft_id)
    posts = store.list_latest_social_posts(campaign.campaign_id)
    social_view = client.get(f"/items/{item_id}?tab=delivery&channel=social")
    wordpress_view = client.get(f"/items/{item_id}?tab=delivery&channel=wordpress")

    assert response.status_code == 303
    assert response.headers["location"].endswith("tab=delivery&channel=social")
    assert campaign.article_draft_id == draft.draft_id
    assert {post.platform for post in posts} == set(SocialPlatform)
    assert "LinkedIn" in social_view.text
    assert "Facebook" in social_view.text
    assert "Buffer · setup needed" in social_view.text
    assert "WordPress · draft only" not in social_view.text
    assert "WordPress · draft only" in wordpress_view.text
    assert "test-social-writer" not in social_view.text


def test_social_edits_are_versioned_and_require_fresh_approval(tmp_path) -> None:
    client, store, packet = _approved_workspace(tmp_path / "editorial.sqlite3")
    item_id = packet.brief.content_item_id
    client.post(f"/items/{item_id}/social")
    draft = store.get_latest_draft(item_id)
    campaign = store.get_social_campaign(draft.draft_id)
    linkedin = next(
        post
        for post in store.list_latest_social_posts(campaign.campaign_id)
        if post.platform == SocialPlatform.LINKEDIN
    )
    client.post(
        f"/items/{item_id}/social/{linkedin.post_id}/decision",
        data={"status": "approved"},
    )

    edited = client.post(
        f"/items/{item_id}/social/linkedin/edit",
        data={
            "base_post_id": linkedin.post_id,
            "body": "Developers can now try the public code-review beta.",
            "source_ids": "S1",
            "edit_note": "Shortened the opening.",
        },
        follow_redirects=False,
    )
    versions = store.list_social_post_versions(campaign.campaign_id, SocialPlatform.LINKEDIN)

    assert edited.status_code == 303
    assert len(versions) == 2
    assert versions[0].parent_post_id == linkedin.post_id
    assert versions[0].version == 2
    assert versions[0].status == SocialPostStatus.DRAFT
    assert versions[1].status == SocialPostStatus.APPROVED


def test_social_feedback_is_reused_by_future_campaigns(tmp_path) -> None:
    client, store, packet = _approved_workspace(tmp_path / "editorial.sqlite3")
    item_id = packet.brief.content_item_id
    client.post(f"/items/{item_id}/social")

    response = client.post(
        f"/items/{item_id}/social/feedback",
        data={
            "platform": "linkedin",
            "signal": "avoid",
            "note": "Avoid generic excitement in the opening.",
        },
        follow_redirects=False,
    )
    social_view = client.get(f"/items/{item_id}?tab=delivery&channel=social")
    preferences = store.list_social_preferences()

    assert response.status_code == 303
    assert preferences["linkedin"] == [
        "Avoid: Avoid generic excitement in the opening."
    ]
    assert "Avoid generic excitement" in social_view.text


def test_social_editor_rejects_stale_unknown_and_overlong_copy(tmp_path) -> None:
    client, store, packet = _approved_workspace(tmp_path / "editorial.sqlite3")
    item_id = packet.brief.content_item_id
    client.post(f"/items/{item_id}/social")
    draft = store.get_latest_draft(item_id)
    campaign = store.get_social_campaign(draft.draft_id)
    x_post = next(
        post
        for post in store.list_latest_social_posts(campaign.campaign_id)
        if post.platform == SocialPlatform.X
    )
    base = {
        "base_post_id": x_post.post_id,
        "body": "Supported copy.",
        "source_ids": "S1",
        "edit_note": "Test edit.",
    }

    unknown = client.post(
        f"/items/{item_id}/social/x/edit",
        data={**base, "source_ids": "S99"},
    )
    overlong = client.post(
        f"/items/{item_id}/social/x/edit",
        data={**base, "body": "x" * 281},
    )
    stale = client.post(
        f"/items/{item_id}/social/x/edit",
        data={**base, "base_post_id": "social_stale"},
    )

    assert unknown.status_code == 400
    assert "unknown evidence IDs" in unknown.text
    assert overlong.status_code == 400
    assert "280-character" in overlong.text
    assert stale.status_code == 409
    assert len(store.list_social_post_versions(campaign.campaign_id, SocialPlatform.X)) == 1


def test_social_generator_rejects_missing_platform_and_unknown_evidence() -> None:
    draft_generator = _StubDraftGenerator()
    packet = _packet()
    draft = asyncio.run(
        draft_generator.generate(packet, type("Profile", (), {"rules": []})())
    )
    missing = SocialCampaignGenerator(
        _FakeClient(
            {
                "posts": [
                    {"platform": "linkedin", "body": "Supported.", "source_ids": ["S1"]},
                    {"platform": "x", "body": "Supported.", "source_ids": ["S1"]},
                    {"platform": "x", "body": "Duplicate.", "source_ids": ["S1"]},
                ]
            }
        ),
        "test-model",
    )
    unknown = SocialCampaignGenerator(
        _FakeClient(
            {
                "posts": [
                    {"platform": "linkedin", "body": "Supported.", "source_ids": ["S1"]},
                    {"platform": "x", "body": "Supported.", "source_ids": ["S99"]},
                    {"platform": "facebook", "body": "Supported.", "source_ids": ["S1"]},
                ]
            }
        ),
        "test-model",
    )

    with pytest.raises(SocialGenerationError, match="one post for each"):
        asyncio.run(missing.generate(packet.brief.content_item_id, draft))
    with pytest.raises(SocialGenerationError, match="unknown evidence IDs"):
        asyncio.run(unknown.generate(packet.brief.content_item_id, draft))
