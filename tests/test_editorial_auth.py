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


def test_role_permissions_keep_workspace_management_out_of_editor_role() -> None:
    editor = AuthPrincipal(authenticated=True, role=WorkspaceRole.EDITOR)
    admin = AuthPrincipal(authenticated=True, role=WorkspaceRole.ADMIN)

    assert editor.can("content:write") is True
    assert editor.can("workspace:manage") is False
    assert admin.can("workspace:manage") is True
