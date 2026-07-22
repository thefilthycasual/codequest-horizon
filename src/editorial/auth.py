"""Authentication boundary prepared for Clerk without coupling the app to its UI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from fastapi import Request


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[str]] = {
    WorkspaceRole.OWNER: frozenset({"workspace:manage", "content:write", "content:approve", "content:read"}),
    WorkspaceRole.ADMIN: frozenset({"workspace:manage", "content:write", "content:approve", "content:read"}),
    WorkspaceRole.EDITOR: frozenset({"content:write", "content:approve", "content:read"}),
    WorkspaceRole.VIEWER: frozenset({"content:read"}),
}

PUBLIC_AUTH_PATHS = frozenset({"/health", "/api/auth/status", "/sign-in", "/discord/interactions"})


def required_permission(method: str, path: str) -> str:
    """Translate an HTTP action into the minimum workspace permission."""

    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return "content:read"
    if path.startswith(("/settings", "/workspace", "/integrations", "/sources")) or path == "/operations/run":
        return "workspace:manage"
    if path.endswith("/decision") or "/decision/" in path:
        return "content:approve"
    return "content:write"


@dataclass(frozen=True)
class AuthConfig:
    provider: str = "local"
    enforce: bool = False
    publishable_key: str = ""
    secret_key: str = ""
    jwt_key: str = ""
    authorized_parties: tuple[str, ...] = ()
    sign_in_url: str = ""
    frontend_api_url: str = ""

    @classmethod
    def from_env(cls) -> "AuthConfig":
        parties = tuple(
            value.strip()
            for value in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
            if value.strip()
        )
        return cls(
            provider=os.getenv("AUTH_PROVIDER", "local").strip().lower() or "local",
            enforce=os.getenv("AUTH_ENFORCEMENT_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            publishable_key=os.getenv("CLERK_PUBLISHABLE_KEY", "").strip(),
            secret_key=os.getenv("CLERK_SECRET_KEY", "").strip(),
            jwt_key=os.getenv("CLERK_JWT_KEY", "").replace("\\n", "\n").strip(),
            authorized_parties=parties,
            sign_in_url=os.getenv("CLERK_SIGN_IN_URL", "").strip(),
            frontend_api_url=os.getenv("CLERK_FRONTEND_API_URL", "").strip().rstrip("/"),
        )

    @property
    def clerk_selected(self) -> bool:
        return self.provider == "clerk"

    @property
    def clerk_ready(self) -> bool:
        return bool(
            self.publishable_key
            and (self.jwt_key or self.secret_key)
            and self.authorized_parties
        )

    @property
    def clerk_frontend_ready(self) -> bool:
        return bool(self.clerk_selected and self.publishable_key and self.frontend_api_url)


@dataclass(frozen=True)
class AuthPrincipal:
    authenticated: bool
    user_id: str = ""
    organization_id: str = ""
    role: WorkspaceRole = WorkspaceRole.VIEWER
    session_id: str = ""
    provider: str = "local"
    reason: str = ""

    def can(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


class RequestAuthenticator(Protocol):
    def authenticate(self, request: Request) -> AuthPrincipal: ...


class LocalRequestAuthenticator:
    """Single-user development identity used until managed auth is enabled."""

    def authenticate(self, request: Request) -> AuthPrincipal:
        del request
        return AuthPrincipal(
            authenticated=True,
            user_id="local_owner",
            organization_id="org_codequest",
            role=WorkspaceRole.OWNER,
            provider="local",
        )


def _normalise_clerk_role(value: str) -> WorkspaceRole:
    role = value.removeprefix("org:").lower()
    return {
        "owner": WorkspaceRole.OWNER,
        "admin": WorkspaceRole.ADMIN,
        "editor": WorkspaceRole.EDITOR,
        "member": WorkspaceRole.VIEWER,
        "viewer": WorkspaceRole.VIEWER,
    }.get(role, WorkspaceRole.VIEWER)


class ClerkRequestAuthenticator:
    """Verify Clerk session tokens with Clerk's official Python backend SDK."""

    def __init__(self, config: AuthConfig):
        self.config = config

    def authenticate(self, request: Request) -> AuthPrincipal:
        if not self.config.clerk_ready:
            return AuthPrincipal(
                authenticated=False,
                provider="clerk",
                reason="Clerk configuration is incomplete.",
            )
        try:
            from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
        except ImportError:
            return AuthPrincipal(
                authenticated=False,
                provider="clerk",
                reason="The Clerk backend package is not installed.",
            )

        try:
            state = authenticate_request(
                request,
                AuthenticateRequestOptions(
                    secret_key=self.config.secret_key or None,
                    jwt_key=self.config.jwt_key or None,
                    authorized_parties=list(self.config.authorized_parties),
                    accepts_token=["session_token"],
                ),
            )
        except Exception:
            return AuthPrincipal(
                authenticated=False,
                provider="clerk",
                reason="The Clerk session could not be verified.",
            )
        if not state.is_signed_in:
            return AuthPrincipal(
                authenticated=False,
                provider="clerk",
                reason="Sign in is required.",
            )
        payload = state.payload or {}
        return AuthPrincipal(
            authenticated=True,
            user_id=str(payload.get("sub", "")),
            organization_id=str(payload.get("org_id", "")),
            role=_normalise_clerk_role(str(payload.get("org_role", "viewer"))),
            session_id=str(payload.get("sid", "")),
            provider="clerk",
        )


def create_request_authenticator(config: AuthConfig) -> RequestAuthenticator:
    if config.clerk_selected:
        return ClerkRequestAuthenticator(config)
    return LocalRequestAuthenticator()
