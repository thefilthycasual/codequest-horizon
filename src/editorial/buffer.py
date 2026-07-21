"""Guarded Buffer GraphQL delivery for approved social copy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .models import (
    BufferDeliveryMode,
    SocialCampaign,
    SocialPlatform,
    SocialPostDraft,
)


BUFFER_API_URL = "https://api.buffer.com"
X_LINK_LENGTH = 23
X_SAFE_LIMIT = 275

CREATE_POST_MUTATION = """mutation CreateCodeQuestPost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post { id status dueAt }
    }
    ... on MutationError { message }
  }
}"""


class BufferDeliveryError(ValueError):
    """Base error for delivery requests that did not produce a recorded post."""


class BufferDeliveryRejected(BufferDeliveryError):
    """A definitive rejection that is safe to retry after correction."""


class BufferDeliveryUncertain(BufferDeliveryError):
    """An ambiguous outcome that must not be retried automatically."""


@dataclass(frozen=True)
class BufferConfig:
    access_token: str
    channel_ids: dict[SocialPlatform, str]
    dry_run: bool = True
    schedule_timezone: str = "Africa/Johannesburg"

    @classmethod
    def from_env(cls) -> "BufferConfig":
        token = os.getenv("BUFFER_ACCESS_TOKEN", "").strip() or os.getenv(
            "BUFFER_API_KEY", ""
        ).strip()
        channels = {
            SocialPlatform.LINKEDIN: os.getenv("BUFFER_CHANNEL_ID_LINKEDIN", "").strip(),
            SocialPlatform.X: os.getenv("BUFFER_CHANNEL_ID_X", "").strip(),
            SocialPlatform.FACEBOOK: os.getenv("BUFFER_CHANNEL_ID_FACEBOOK", "").strip(),
        }
        missing = [platform.value for platform, value in channels.items() if not value]
        if not token:
            raise BufferDeliveryRejected("BUFFER_ACCESS_TOKEN is required for Buffer delivery.")
        if missing:
            raise BufferDeliveryRejected(
                "Missing Buffer channel IDs for: " + ", ".join(missing) + "."
            )
        schedule_timezone = os.getenv(
            "BUFFER_SCHEDULE_TIMEZONE", "Africa/Johannesburg"
        ).strip()
        try:
            ZoneInfo(schedule_timezone)
        except ZoneInfoNotFoundError as exc:
            raise BufferDeliveryRejected(
                "BUFFER_SCHEDULE_TIMEZONE must be a valid IANA timezone."
            ) from exc
        return cls(
            access_token=token,
            channel_ids=channels,
            dry_run=_env_flag("BUFFER_DRY_RUN", default=True),
            schedule_timezone=schedule_timezone,
        )


@dataclass(frozen=True)
class BufferPostResult:
    post_id: str
    status: str
    due_at: datetime | None = None


def normalize_public_article_url(value: str, wordpress_base_url: str) -> str:
    """Accept only a canonical HTTPS URL on the configured WordPress host."""

    candidate = urlsplit(value.strip())
    expected = urlsplit(wordpress_base_url.strip())
    if (
        candidate.scheme != "https"
        or not candidate.hostname
        or candidate.username
        or candidate.password
    ):
        raise ValueError("The public article URL must be a normal HTTPS URL.")
    if not expected.hostname:
        raise ValueError("WORDPRESS_BASE_URL is required before confirming an article URL.")
    if candidate.hostname.casefold() != expected.hostname.casefold():
        raise ValueError("The public article URL must use the configured WordPress domain.")
    if candidate.fragment:
        candidate = candidate._replace(fragment="")
    return urlunsplit(candidate)


def parse_scheduled_time(value: str, timezone_name: str) -> datetime:
    """Interpret a browser-local datetime in the explicitly configured timezone."""

    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Choose a date and time for the scheduled post.")
    try:
        parsed = datetime.fromisoformat(cleaned)
        local_zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Choose a valid scheduled date and time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_zone)
    scheduled = parsed.astimezone(timezone.utc)
    if scheduled <= datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("Scheduled posts must be at least two minutes in the future.")
    return scheduled


def build_buffer_text(post: SocialPostDraft, campaign: SocialCampaign) -> str:
    if campaign.public_article_url is None:
        raise ValueError("Confirm the public article URL before preparing Buffer delivery.")
    final_text = f"{post.body.strip()}\n\n{campaign.public_article_url}"
    if post.platform == SocialPlatform.X:
        effective_length = len(post.body.strip()) + 2 + X_LINK_LENGTH
        if effective_length > X_SAFE_LIMIT:
            raise ValueError(
                "X copy is too long once the article link is included. Shorten this social version."
            )
    return final_text


def build_buffer_payload(
    post: SocialPostDraft,
    campaign: SocialCampaign,
    config: BufferConfig,
    mode: BufferDeliveryMode,
    scheduled_for: datetime | None = None,
) -> dict[str, object]:
    if post.campaign_id != campaign.campaign_id:
        raise ValueError("Social copy does not belong to this campaign.")
    if mode == BufferDeliveryMode.CUSTOM_SCHEDULED and scheduled_for is None:
        raise ValueError("Scheduled Buffer delivery requires a date and time.")
    if mode == BufferDeliveryMode.SHARE_NOW and scheduled_for is not None:
        raise ValueError("Send-now Buffer delivery cannot include a scheduled time.")
    post_input: dict[str, object] = {
        "text": build_buffer_text(post, campaign),
        "channelId": config.channel_ids[post.platform],
        "schedulingType": "automatic",
        "mode": mode.value,
        "aiAssisted": True,
    }
    if scheduled_for is not None:
        post_input["dueAt"] = scheduled_for.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    return {"query": CREATE_POST_MUTATION, "variables": {"input": post_input}}


class BufferPublisher:
    """Perform one non-retried GraphQL mutation and classify its outcome."""

    def __init__(self, config: BufferConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client

    async def create_post(self, payload: dict[str, object]) -> BufferPostResult:
        if self.config.dry_run:
            raise BufferDeliveryRejected(
                "Buffer delivery is disabled while BUFFER_DRY_RUN is true."
            )
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=20.0)
        try:
            try:
                response = await client.post(
                    BUFFER_API_URL,
                    headers={
                        "Authorization": f"Bearer {self.config.access_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise BufferDeliveryUncertain(
                    "Buffer did not return a conclusive response. Check Buffer before retrying."
                ) from exc
            if response.status_code >= 500:
                raise BufferDeliveryUncertain(
                    "Buffer returned a server error after submission. Check Buffer before retrying."
                )
            if response.status_code >= 400:
                raise BufferDeliveryRejected(
                    "Buffer rejected the request. Check the API key and channel configuration."
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise BufferDeliveryUncertain(
                    "Buffer returned an unreadable response. Check Buffer before retrying."
                ) from exc
            mutation = (body.get("data") or {}).get("createPost") or {}
            returned_post = mutation.get("post")
            if returned_post and returned_post.get("id"):
                return BufferPostResult(
                    post_id=str(returned_post["id"]),
                    status=str(returned_post.get("status") or "buffer"),
                    due_at=_optional_datetime(returned_post.get("dueAt")),
                )
            if mutation.get("message"):
                raise BufferDeliveryRejected(str(mutation["message"]))
            if body.get("errors"):
                raise BufferDeliveryUncertain(
                    "Buffer returned a GraphQL execution error. Check Buffer before retrying."
                )
            raise BufferDeliveryUncertain(
                "Buffer returned no post or rejection. Check Buffer before retrying."
            )
        finally:
            if owns_client:
                await client.aclose()


def _optional_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}
