"""Discord delivery and signed interaction handling for final draft approval."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .models import ArticleDraft, DiscordApprovalRequest, DraftQualityReport


DISCORD_API_BASE = "https://discord.com/api/v10"


@dataclass(frozen=True)
class DiscordConfig:
    bot_token: str
    channel_id: str
    public_key: str

    @classmethod
    def from_env(cls) -> "DiscordConfig":
        values = {
            "bot_token": os.getenv("DISCORD_BOT_TOKEN", "").strip(),
            "channel_id": os.getenv("DISCORD_APPROVAL_CHANNEL_ID", "").strip(),
            "public_key": os.getenv("DISCORD_APPLICATION_PUBLIC_KEY", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            names = ", ".join(name.upper() for name in missing)
            raise ValueError(f"Discord approval is not configured: missing {names}.")
        return cls(**values)


def verify_discord_signature(
    public_key: str, signature: str, timestamp: str, body: bytes
) -> bool:
    """Verify Discord's Ed25519 request signature before reading an interaction."""

    if not public_key or not signature or not timestamp:
        return False
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            timestamp.encode("utf-8") + body,
            bytes.fromhex(signature),
        )
    except (BadSignatureError, ValueError):
        return False
    return True


def approval_message_payload(
    request: DiscordApprovalRequest,
    draft: ArticleDraft,
    quality: DraftQualityReport,
) -> dict[str, Any]:
    warnings = sum(check.status.value == "warning" for check in quality.checks)
    body_preview = "\n\n".join(
        paragraph.text
        for section in draft.sections
        for paragraph in section.paragraphs
    )
    if len(body_preview) > 1900:
        body_preview = body_preview[:1897].rstrip() + "…"
    return {
        "content": "A CodeQuest article is ready for optional Discord feedback. Final approval happens in the workspace.",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": draft.title[:256],
                "description": draft.dek[:4096],
                "color": 0xF47A2A,
                "fields": [
                    {"name": "Draft", "value": draft.draft_id, "inline": False},
                    {
                        "name": "Quality gate",
                        "value": f"Passed · {warnings} warning(s)",
                        "inline": True,
                    },
                    {
                        "name": "Sources",
                        "value": str(len(draft.source_map)),
                        "inline": True,
                    },
                    {"name": "Article preview", "value": body_preview or "No preview"},
                ],
                "footer": {"text": f"Request {request.request_id}"},
                "timestamp": request.requested_at.isoformat(),
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 3,
                        "label": "Looks good",
                        "custom_id": f"cq:approve:{request.request_id}",
                    },
                    {
                        "type": 2,
                        "style": 4,
                        "label": "Request changes",
                        "custom_id": f"cq:revise:{request.request_id}",
                    },
                ],
            }
        ],
    }


class DiscordApprovalBridge:
    def __init__(
        self,
        config: DiscordConfig,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self._client = client

    async def test_connection(self) -> str:
        """Read the bot identity without sending or changing any Discord message."""

        headers = {"Authorization": f"Bot {self.config.bot_token}"}
        if self._client is not None:
            response = await self._client.get(
                f"{DISCORD_API_BASE}/users/@me", headers=headers
            )
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{DISCORD_API_BASE}/users/@me", headers=headers
                )
        response.raise_for_status()
        payload = response.json()
        username = str(payload.get("username") or "Discord bot")
        return username[:80]

    async def send_request(
        self,
        request: DiscordApprovalRequest,
        draft: ArticleDraft,
        quality: DraftQualityReport,
    ) -> tuple[str, str]:
        payload = approval_message_payload(request, draft, quality)
        headers = {"Authorization": f"Bot {self.config.bot_token}"}
        if self._client is not None:
            response = await self._client.post(
                f"{DISCORD_API_BASE}/channels/{self.config.channel_id}/messages",
                headers=headers,
                json=payload,
            )
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{DISCORD_API_BASE}/channels/{self.config.channel_id}/messages",
                    headers=headers,
                    json=payload,
                )
        response.raise_for_status()
        return self.config.channel_id, str(response.json()["id"])

    async def close_request_message(
        self, request: DiscordApprovalRequest, summary: str
    ) -> None:
        if not request.channel_id or not request.message_id:
            return
        payload = {
            "content": summary,
            "components": [],
            "allowed_mentions": {"parse": []},
        }
        headers = {"Authorization": f"Bot {self.config.bot_token}"}
        url = (
            f"{DISCORD_API_BASE}/channels/{request.channel_id}/messages/"
            f"{request.message_id}"
        )
        if self._client is not None:
            response = await self._client.patch(url, headers=headers, json=payload)
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.patch(url, headers=headers, json=payload)
        response.raise_for_status()


def revision_modal(request_id: str) -> dict[str, Any]:
    return {
        "type": 9,
        "data": {
            "custom_id": f"cq:revision_modal:{request_id}",
            "title": "Request article changes",
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": "revision_notes",
                            "label": "What should change?",
                            "style": 2,
                            "min_length": 3,
                            "max_length": 1000,
                            "required": True,
                        }
                    ],
                }
            ],
        },
    }


def modal_value(payload: dict[str, Any], custom_id: str) -> str:
    for row in payload.get("data", {}).get("components", []):
        candidates = row.get("components", [])
        if row.get("component"):
            candidates = [row["component"]]
        for component in candidates:
            if component.get("custom_id") == custom_id:
                return str(component.get("value", "")).strip()
    return ""
