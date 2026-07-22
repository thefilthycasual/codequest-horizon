from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from src.editorial.web import create_app


def test_sidebar_keeps_administration_inside_profile_settings_menu(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    page = client.get("/")
    html = BeautifulSoup(page.text, "html.parser")
    primary_links = {
        link.get("href") for link in html.select("nav.side-nav a")
    }
    account_menu = html.select_one(".account-menu")

    assert page.status_code == 200
    assert primary_links == {
        "/",
        "/discovery",
        "/sources",
        "/editorial",
        "/drafts",
        "/publishing",
        "/preferences",
        "/operations",
    }
    assert account_menu is not None
    assert "Local owner" in account_menu.get_text(" ", strip=True)
    assert account_menu.select_one("a[href='/settings/profile']") is not None
    assert account_menu.select_one("a[href='/settings/workspace']") is not None
    assert account_menu.select_one("a[href='/settings/integrations']") is not None
    assert account_menu.select_one("a[href='/settings/access']") is not None
    assert account_menu.select_one("a[href='/settings/billing']") is not None


def test_settings_home_groups_account_and_administration_surfaces(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    page = client.get("/settings")

    assert page.status_code == 200
    assert "Manage your account" in page.text
    assert "Your profile" in page.text
    assert "Workspace &amp; brands" in page.text
    assert "Integrations &amp; API keys" in page.text
    assert "Team &amp; access" in page.text
    assert "Billing" in page.text


def test_legacy_administration_pages_redirect_to_settings(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    workspace = client.get("/workspace", follow_redirects=False)
    integrations = client.get("/integrations?tab=security", follow_redirects=False)
    access = client.get("/access", follow_redirects=False)

    assert workspace.status_code == 308
    assert workspace.headers["location"] == "/settings/workspace"
    assert integrations.status_code == 308
    assert integrations.headers["location"] == "/settings/integrations?tab=security"
    assert access.status_code == 308
    assert access.headers["location"] == "/settings/access"


def test_billing_is_an_explicit_future_settings_boundary(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "editorial.sqlite3"))

    page = client.get("/settings/billing")

    assert page.status_code == 200
    assert "Subscription and" in page.text
    assert "no payment provider is connected yet" in page.text
    assert "Stripe Billing" in page.text
