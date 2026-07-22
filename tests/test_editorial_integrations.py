import asyncio

import httpx
from fastapi.testclient import TestClient

from src.editorial.discord import DiscordApprovalBridge, DiscordConfig
from src.editorial.image_generation import ImageGenerationConfig
from src.editorial.integrations import IntegrationState, integration_inventory
from src.editorial.web import create_app

from test_editorial_wordpress import _StubPublisher


INTEGRATION_ENV_NAMES = (
    "OLLAMA_BASE_URL",
    "OLLAMA_API_KEY",
    "OLLAMA_MODEL_WRITER",
    "WORDPRESS_BASE_URL",
    "WORDPRESS_USERNAME",
    "WORDPRESS_APP_PASSWORD",
    "WORDPRESS_DRY_RUN",
    "BUFFER_ACCESS_TOKEN",
    "BUFFER_API_KEY",
    "BUFFER_CHANNEL_ID_LINKEDIN",
    "BUFFER_CHANNEL_ID_X",
    "BUFFER_CHANNEL_ID_FACEBOOK",
    "BUFFER_DRY_RUN",
    "DISCORD_BOT_TOKEN",
    "DISCORD_APPROVAL_CHANNEL_ID",
    "DISCORD_APPLICATION_PUBLIC_KEY",
    "IMAGE_GENERATION_ENABLED",
    "IMAGE_GENERATION_PROVIDER",
    "IMAGE_GENERATION_MODEL",
    "OPENAI_API_KEY",
    "AUTH_PROVIDER",
    "AUTH_ENFORCEMENT_ENABLED",
    "CLERK_PUBLISHABLE_KEY",
    "CLERK_SECRET_KEY",
    "CLERK_JWT_KEY",
    "CLERK_AUTHORIZED_PARTIES",
    "CLERK_FRONTEND_API_URL",
    "CLERK_SIGN_IN_URL",
)


def _clear_integration_env(monkeypatch) -> None:
    for name in INTEGRATION_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_integration_inventory_reports_states_without_secret_values(
    tmp_path, monkeypatch
) -> None:
    _clear_integration_env(monkeypatch)
    sentinel = "never-render-this-secret"
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://wordpress.example")
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", sentinel)
    monkeypatch.setenv("WORDPRESS_DRY_RUN", "true")
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "false")
    config_path = tmp_path / "config.json"
    config_path.write_text("{}")

    inventory = integration_inventory(discovery_config_path=config_path)
    statuses = {item.key: item for item in inventory}

    assert statuses["wordpress"].state == IntegrationState.GUARDED
    assert statuses["wordpress"].state_label == "Safe mode"
    assert statuses["images"].state == IntegrationState.OFF
    assert statuses["discord"].state == IntegrationState.OFF
    assert statuses["horizon"].state == IntegrationState.READY
    assert statuses["clerk"].state == IntegrationState.OFF
    assert sentinel not in repr(inventory)
    assert "WORDPRESS_APP_PASSWORD" in statuses["wordpress"].configuration_names


def test_integrations_dashboard_is_nontechnical_and_redacts_credentials(
    tmp_path, monkeypatch
) -> None:
    _clear_integration_env(monkeypatch)
    sentinel = "dashboard-must-not-return-this"
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example")
    monkeypatch.setenv("OLLAMA_API_KEY", sentinel)
    monkeypatch.setenv("OLLAMA_MODEL_WRITER", "writer-model")
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://wordpress.example")
    monkeypatch.setenv("WORDPRESS_USERNAME", "editor")
    monkeypatch.setenv("WORDPRESS_APP_PASSWORD", sentinel)
    monkeypatch.setenv("WORDPRESS_DRY_RUN", "true")
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            source_config_path=tmp_path / "missing-config.json",
        )
    )

    page = client.get("/integrations")
    security = client.get("/integrations?tab=security")

    assert page.status_code == 200
    assert "Every connection" in page.text
    assert "WordPress" in page.text
    assert "Safe mode" in page.text
    assert "Test read-only connection" in page.text
    assert "Values are read from protected runtime secrets" in page.text
    assert "Integrations" in page.text
    assert sentinel not in page.text
    assert "Credentials do not belong in editorial data" in security.text
    assert "Infisical" in security.text
    assert "OpenBao" in security.text
    assert sentinel not in security.text


def test_integration_checks_do_not_generate_or_write_content(tmp_path, monkeypatch) -> None:
    _clear_integration_env(monkeypatch)
    wordpress = _StubPublisher()
    image_factory_calls = []

    def image_factory():
        image_factory_calls.append("validated")
        return object()

    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            wordpress_publisher_factory=lambda: wordpress,
            image_generator_factory=image_factory,
            image_config_factory=lambda: ImageGenerationConfig(enabled=False),
            source_config_path=tmp_path / "missing-config.json",
        )
    )

    wordpress_check = client.post(
        "/integrations/wordpress/test", follow_redirects=False
    )
    image_check = client.post("/integrations/images/test", follow_redirects=False)

    assert wordpress_check.status_code == 303
    assert "WordPress%20responded%20successfully" in wordpress_check.headers["location"]
    assert wordpress.calls == 0
    assert wordpress.uploaded is None
    assert image_check.status_code == 303
    assert image_factory_calls == ["validated"]


def test_discord_connection_check_reads_identity_without_sending_message() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        return httpx.Response(200, json={"id": "42", "username": "CodeQuest Bot"})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DiscordApprovalBridge(
                DiscordConfig("token", "channel", "public-key"), client
            ).test_connection()

    name = asyncio.run(check())

    assert name == "CodeQuest Bot"
    assert requests == [("GET", "https://discord.com/api/v10/users/@me")]
