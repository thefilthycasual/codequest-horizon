from fastapi.testclient import TestClient

from src.editorial.auth import AuthConfig, AuthPrincipal, WorkspaceRole
from src.editorial.web import create_app


class _StubAuthenticator:
    def __init__(self, principal: AuthPrincipal):
        self.principal = principal

    def authenticate(self, request):
        del request
        return self.principal


def test_local_mode_exposes_owner_context_without_clerk(tmp_path) -> None:
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            auth_config=AuthConfig(),
        )
    )

    status = client.get("/api/auth/status")
    access = client.get("/access")

    assert status.status_code == 200
    assert status.json() == {
        "provider": "local",
        "enforced": False,
        "configured": False,
        "authenticated": True,
        "role": "owner",
        "organization_selected": True,
        "frontend_ready": False,
        "permissions": [
            "content:approve",
            "content:read",
            "content:write",
            "workspace:manage",
        ],
    }
    assert access.status_code == 200
    assert "Local owner mode" in access.text
    assert "Three deliberate steps" in access.text


def test_clerk_preview_attaches_verified_organization_role(tmp_path) -> None:
    principal = AuthPrincipal(
        authenticated=True,
        user_id="user_123",
        organization_id="org_123",
        role=WorkspaceRole.EDITOR,
        session_id="sess_123",
        provider="clerk",
    )
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            auth_config=AuthConfig(
                provider="clerk",
                publishable_key="pk_test_safe",
                jwt_key="public-key",
                authorized_parties=("https://app.example",),
            ),
            request_authenticator=_StubAuthenticator(principal),
        )
    )

    status = client.get("/api/auth/status")

    assert status.status_code == 200
    assert status.json()["authenticated"] is True
    assert status.json()["role"] == "editor"
    assert status.json()["organization_selected"] is True


def test_clerk_enforcement_rejects_anonymous_requests_but_keeps_status_safe(
    tmp_path,
) -> None:
    anonymous = AuthPrincipal(
        authenticated=False,
        provider="clerk",
        reason="Sign in is required.",
    )
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            auth_config=AuthConfig(provider="clerk", enforce=True),
            request_authenticator=_StubAuthenticator(anonymous),
        )
    )

    protected = client.get("/", headers={"accept": "application/json"})
    status = client.get("/api/auth/status")

    assert protected.status_code == 401
    assert protected.json() == {"detail": "Sign in is required."}
    assert status.status_code == 200
    assert status.json()["authenticated"] is False


def test_clerk_frontend_mounts_account_controls_without_backend_secrets(tmp_path) -> None:
    config = AuthConfig(
        provider="clerk",
        publishable_key="pk_test_public_value",
        secret_key="must-never-reach-browser",
        frontend_api_url="https://clerk.example",
    )
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            auth_config=config,
            request_authenticator=_StubAuthenticator(
                AuthPrincipal(authenticated=False, provider="clerk")
            ),
        )
    )

    page = client.get("/access")
    sign_in = client.get("/sign-in")

    assert page.status_code == 200
    assert "clerk.example/npm/@clerk/clerk-js@6" in page.text
    assert "pk_test_public_value" in page.text
    assert "clerk-organization-switcher" in page.text
    assert "clerk-user-button" in page.text
    assert "must-never-reach-browser" not in page.text
    assert "clerk-sign-in" in sign_in.text
    assert "must-never-reach-browser" not in sign_in.text


def test_enforced_clerk_roles_protect_workspace_management(tmp_path) -> None:
    editor = AuthPrincipal(
        authenticated=True,
        user_id="user_editor",
        organization_id="org_clerk",
        role=WorkspaceRole.EDITOR,
        provider="clerk",
    )
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            auth_config=AuthConfig(provider="clerk", enforce=True),
            request_authenticator=_StubAuthenticator(editor),
        )
    )

    assert client.get("/workspace").status_code == 200
    denied = client.post("/workspace/switch", data={"brand_id": "brand_codequest"})
    assert denied.status_code == 403
    assert denied.json() == {"detail": "Your workspace role does not allow this action."}


def test_enforced_clerk_requires_an_active_organization(tmp_path) -> None:
    principal = AuthPrincipal(
        authenticated=True,
        user_id="user_without_org",
        role=WorkspaceRole.ADMIN,
        provider="clerk",
    )
    client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            auth_config=AuthConfig(provider="clerk", enforce=True),
            request_authenticator=_StubAuthenticator(principal),
        )
    )

    denied = client.get("/")

    assert denied.status_code == 403
    assert denied.json() == {
        "detail": "Select a Clerk organisation to open this workspace."
    }


def test_role_permissions_keep_workspace_management_out_of_editor_role() -> None:
    editor = AuthPrincipal(authenticated=True, role=WorkspaceRole.EDITOR)
    admin = AuthPrincipal(authenticated=True, role=WorkspaceRole.ADMIN)

    assert editor.can("content:write") is True
    assert editor.can("workspace:manage") is False
    assert admin.can("workspace:manage") is True
