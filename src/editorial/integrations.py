"""Secret-safe inventory of external services used by the editorial workspace."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit


class IntegrationState(str, Enum):
    READY = "ready"
    GUARDED = "guarded"
    LIVE = "live"
    OFF = "off"
    NEEDS_SETUP = "needs_setup"


@dataclass(frozen=True)
class IntegrationStatus:
    key: str
    name: str
    purpose: str
    state: IntegrationState
    state_label: str
    summary: str
    details: tuple[str, ...]
    configuration_names: tuple[str, ...]
    test_label: str
    optional: bool = False


def _value(name: str) -> str:
    return os.getenv(name, "").strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _host(value: str) -> str:
    return (urlsplit(value).hostname or "Not configured").lower()


def _configured_state(
    values: tuple[str, ...],
    *,
    optional: bool = False,
) -> IntegrationState:
    present = sum(bool(value) for value in values)
    if present == len(values):
        return IntegrationState.READY
    if present == 0 and optional:
        return IntegrationState.OFF
    return IntegrationState.NEEDS_SETUP


def integration_inventory(
    *,
    discovery_config_path: str | Path = "data/config.json",
) -> list[IntegrationStatus]:
    """Describe configuration without returning any credential value or fingerprint."""

    ollama_url = _value("OLLAMA_BASE_URL")
    ollama_model = _value("OLLAMA_MODEL_WRITER")
    ollama_state = _configured_state(
        (ollama_url, _value("OLLAMA_API_KEY"), ollama_model)
    )

    wordpress_url = _value("WORDPRESS_BASE_URL")
    wordpress_values = (
        wordpress_url,
        _value("WORDPRESS_USERNAME"),
        _value("WORDPRESS_APP_PASSWORD"),
    )
    wordpress_state = _configured_state(wordpress_values)
    wordpress_dry_run = _flag("WORDPRESS_DRY_RUN", default=True)
    if wordpress_state == IntegrationState.READY:
        wordpress_state = (
            IntegrationState.GUARDED
            if wordpress_dry_run
            else IntegrationState.LIVE
        )

    buffer_channels = (
        _value("BUFFER_CHANNEL_ID_LINKEDIN"),
        _value("BUFFER_CHANNEL_ID_X"),
        _value("BUFFER_CHANNEL_ID_FACEBOOK"),
    )
    buffer_values = (
        _value("BUFFER_ACCESS_TOKEN") or _value("BUFFER_API_KEY"),
        *buffer_channels,
    )
    buffer_state = _configured_state(buffer_values)
    buffer_dry_run = _flag("BUFFER_DRY_RUN", default=True)
    if buffer_state == IntegrationState.READY:
        buffer_state = (
            IntegrationState.GUARDED if buffer_dry_run else IntegrationState.LIVE
        )

    discord_values = (
        _value("DISCORD_BOT_TOKEN"),
        _value("DISCORD_APPROVAL_CHANNEL_ID"),
        _value("DISCORD_APPLICATION_PUBLIC_KEY"),
    )
    discord_state = _configured_state(discord_values, optional=True)

    image_enabled = _flag("IMAGE_GENERATION_ENABLED", default=False)
    image_provider = _value("IMAGE_GENERATION_PROVIDER") or "openai"
    image_provider_label = (
        "OpenAI" if image_provider == "openai" else image_provider.title()
    )
    image_model = _value("IMAGE_GENERATION_MODEL") or "gpt-image-2"
    if not image_enabled:
        image_state = IntegrationState.OFF
    else:
        image_state = _configured_state(
            (image_provider, image_model, _value("OPENAI_API_KEY"))
        )

    discovery_path = Path(discovery_config_path)
    horizon_state = (
        IntegrationState.READY
        if discovery_path.is_file()
        else IntegrationState.NEEDS_SETUP
    )
    automation_enabled = _flag("AUTOMATION_ENABLED", default=False)

    state_labels = {
        IntegrationState.READY: "Ready",
        IntegrationState.GUARDED: "Safe mode",
        IntegrationState.LIVE: "Live writes enabled",
        IntegrationState.OFF: "Optional · off",
        IntegrationState.NEEDS_SETUP: "Setup needed",
    }

    return [
        IntegrationStatus(
            key="horizon",
            name="Horizon Discovery",
            purpose="Find and rank source material for the editorial queue.",
            state=horizon_state,
            state_label=state_labels[horizon_state],
            summary=(
                "Source policy is available."
                if horizon_state == IntegrationState.READY
                else "Create or repair the discovery policy before running imports."
            ),
            details=(
                f"Policy file: {discovery_path.name}",
                f"Scheduled discovery: {'on' if automation_enabled else 'off'}",
            ),
            configuration_names=("AUTOMATION_DISCOVERY_CONFIG", "AUTOMATION_ENABLED"),
            test_label="Validate source setup",
        ),
        IntegrationStatus(
            key="ollama",
            name="Ollama Cloud",
            purpose="Generate evidence-grounded articles and social drafts.",
            state=ollama_state,
            state_label=state_labels[ollama_state],
            summary=(
                "Cloud writer configuration is complete."
                if ollama_state == IntegrationState.READY
                else "Add the cloud endpoint, API key, and writer model."
            ),
            details=(
                f"Endpoint: {_host(ollama_url)}",
                f"Writer model: {ollama_model or 'Not configured'}",
                f"Automatic drafting: {'on' if _flag('AUTOMATION_AUTO_GENERATE') else 'off'}",
            ),
            configuration_names=(
                "OLLAMA_BASE_URL",
                "OLLAMA_API_KEY",
                "OLLAMA_MODEL_WRITER",
            ),
            test_label="Validate AI setup",
        ),
        IntegrationStatus(
            key="wordpress",
            name="WordPress",
            purpose="Sync categories and media, then create unpublished article drafts.",
            state=wordpress_state,
            state_label=state_labels[wordpress_state],
            summary=(
                "Draft creation is locked by dry-run mode."
                if wordpress_state == IntegrationState.GUARDED
                else "Explicit draft creation can write to WordPress."
                if wordpress_state == IntegrationState.LIVE
                else "Add the site URL, username, and application password."
            ),
            details=(
                f"Website: {_host(wordpress_url)}",
                f"Draft delivery: {'preview only' if wordpress_dry_run else 'explicit writes enabled'}",
                "Publishing: always manual",
            ),
            configuration_names=(
                "WORDPRESS_BASE_URL",
                "WORDPRESS_USERNAME",
                "WORDPRESS_APP_PASSWORD",
                "WORDPRESS_DRY_RUN",
            ),
            test_label="Test read-only connection",
        ),
        IntegrationStatus(
            key="images",
            name="AI Image Provider",
            purpose="Create local featured-image candidates for human review.",
            state=image_state,
            state_label=state_labels[image_state],
            summary=(
                "Generation is deliberately disabled."
                if image_state == IntegrationState.OFF
                else "The provider is ready for explicit, paid generation requests."
                if image_state == IntegrationState.READY
                else "Enable generation and provide the selected provider credential."
            ),
            details=(
                f"Provider: {image_provider_label}",
                f"Model: {image_model}",
                "Automatic generation: never",
            ),
            configuration_names=(
                "IMAGE_GENERATION_ENABLED",
                "IMAGE_GENERATION_PROVIDER",
                "IMAGE_GENERATION_MODEL",
                "OPENAI_API_KEY",
            ),
            test_label="Validate image setup",
            optional=True,
        ),
        IntegrationStatus(
            key="buffer",
            name="Buffer",
            purpose="Deliver approved social posts to connected organisation channels.",
            state=buffer_state,
            state_label=state_labels[buffer_state],
            summary=(
                "Delivery is locked in preview mode."
                if buffer_state == IntegrationState.GUARDED
                else "Explicit social delivery can write to Buffer."
                if buffer_state == IntegrationState.LIVE
                else "Add the Buffer token and all required channel IDs."
            ),
            details=(
                f"Channels configured: {sum(bool(value) for value in buffer_channels)} of 3",
                f"Delivery: {'preview only' if buffer_dry_run else 'explicit writes enabled'}",
                f"Timezone: {_value('BUFFER_SCHEDULE_TIMEZONE') or 'Africa/Johannesburg'}",
            ),
            configuration_names=(
                "BUFFER_ACCESS_TOKEN",
                "BUFFER_CHANNEL_ID_LINKEDIN",
                "BUFFER_CHANNEL_ID_X",
                "BUFFER_CHANNEL_ID_FACEBOOK",
                "BUFFER_DRY_RUN",
            ),
            test_label="Validate Buffer setup",
        ),
        IntegrationStatus(
            key="discord",
            name="Discord",
            purpose="Share optional team feedback without granting approval authority.",
            state=discord_state,
            state_label=state_labels[discord_state],
            summary=(
                "Discord advisory feedback is ready."
                if discord_state == IntegrationState.READY
                else "Discord is not in use. The workspace remains authoritative."
                if discord_state == IntegrationState.OFF
                else "Complete all Discord bot and interaction settings."
            ),
            details=(
                "Authority: advisory only",
                f"Approval authority: workspace",
                f"Configuration fields present: {sum(bool(value) for value in discord_values)} of 3",
            ),
            configuration_names=(
                "DISCORD_BOT_TOKEN",
                "DISCORD_APPROVAL_CHANNEL_ID",
                "DISCORD_APPLICATION_PUBLIC_KEY",
            ),
            test_label="Test bot identity",
            optional=True,
        ),
    ]
