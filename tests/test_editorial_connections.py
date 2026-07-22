import base64

from fastapi.testclient import TestClient

from src.editorial.models import (
    BrandProfile,
    ConnectionProvider,
    Organization,
    WordPressMediaItem,
)
from src.editorial.secret_vault import WorkspaceSecretVault
from src.editorial.store import EditorialStore
from src.editorial.web import create_app


def _unlock_vault(monkeypatch) -> WorkspaceSecretVault:
    key = base64.urlsafe_b64encode(bytes(range(32))).decode()
    monkeypatch.setenv("WORKSPACE_SECRET_KEY", key)
    return WorkspaceSecretVault.from_env()


def test_brand_connection_credentials_are_encrypted_and_never_rendered(
    tmp_path, monkeypatch
) -> None:
    vault = _unlock_vault(monkeypatch)
    db_path = tmp_path / "editorial.sqlite3"
    store = EditorialStore(db_path)
    client = TestClient(create_app(db_path))
    password = "wordpress-application-password"

    saved = client.post(
        "/integrations/wordpress/configure",
        data={
            "base_url": "https://brand.example",
            "username": "editor",
            "secret": password,
            "dry_run": "true",
        },
        follow_redirects=False,
    )
    profile = store.get_brand_connection(ConnectionProvider.WORDPRESS)

    assert saved.status_code == 303
    assert profile is not None
    assert password not in profile.model_dump_json()
    assert profile.settings["base_url"] == "https://brand.example"
    assert profile.configured_secret_names == ["application_password"]
    assert vault.decrypt(profile.encrypted_secrets) == {
        "application_password": password
    }
    setup_page = client.get("/integrations?tab=setup")
    assert password not in setup_page.text
    assert "Leave blank to keep the saved credential" in setup_page.text


def test_connections_and_wordpress_media_are_isolated_between_brands(
    tmp_path, monkeypatch
) -> None:
    _unlock_vault(monkeypatch)
    db_path = tmp_path / "editorial.sqlite3"
    store = EditorialStore(db_path)
    store.upsert_wordpress_media(
        WordPressMediaItem(
            media_id=42,
            title="CodeQuest image",
            source_url="https://codequest.example/image.png",
            mime_type="image/png",
        )
    )
    client = TestClient(create_app(db_path))
    client.post(
        "/integrations/images/configure",
        data={
            "base_url": "https://api.openai.com/v1",
            "image_model": "gpt-image-2",
            "secret": "image-key",
            "enabled": "true",
        },
    )
    organization = store.add_organization(Organization(name="Separate organisation"))
    other_brand = store.add_brand(
        BrandProfile(organization_id=organization.organization_id, name="Other brand")
    )
    store.set_active_brand(other_brand.brand_id)

    assert store.get_brand_connection(ConnectionProvider.IMAGES) is None
    assert store.list_wordpress_media() == []
    assert "Brand-specific" not in client.get("/integrations").text

    store.set_active_brand("brand_codequest")
    assert store.get_brand_connection(ConnectionProvider.IMAGES) is not None
    assert [item.media_id for item in store.list_wordpress_media()] == [42]
    assert "Brand-specific" in client.get("/integrations").text


def test_locked_vault_disables_brand_credential_forms(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("WORKSPACE_SECRET_KEY", raising=False)
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    page = client.get("/integrations?tab=setup")
    rejected = client.post(
        "/integrations/wordpress/configure",
        data={
            "base_url": "https://brand.example",
            "username": "editor",
            "secret": "must-not-save",
        },
    )

    assert "Vault locked" in page.text
    assert "disabled" in page.text
    assert rejected.status_code == 409
