"""Small encrypted credential boundary for per-brand connection profiles."""

from __future__ import annotations

import base64
import json
import os

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox


class SecretVaultError(ValueError):
    """Raised when the workspace vault is locked or ciphertext is invalid."""


class WorkspaceSecretVault:
    def __init__(self, key: bytes | None):
        self._box = SecretBox(key) if key is not None else None

    @classmethod
    def from_env(cls) -> "WorkspaceSecretVault":
        encoded = os.getenv("WORKSPACE_SECRET_KEY", "").strip()
        if not encoded:
            return cls(None)
        try:
            key = base64.urlsafe_b64decode(encoded.encode())
        except (ValueError, TypeError) as exc:
            raise SecretVaultError(
                "WORKSPACE_SECRET_KEY must be a URL-safe base64-encoded 32-byte key."
            ) from exc
        if len(key) != SecretBox.KEY_SIZE:
            raise SecretVaultError(
                "WORKSPACE_SECRET_KEY must decode to exactly 32 bytes."
            )
        return cls(key)

    @property
    def ready(self) -> bool:
        return self._box is not None

    def encrypt(self, secrets: dict[str, str]) -> str:
        if self._box is None:
            raise SecretVaultError(
                "The credential vault is locked. Configure WORKSPACE_SECRET_KEY first."
            )
        payload = json.dumps(secrets, sort_keys=True).encode()
        return base64.urlsafe_b64encode(self._box.encrypt(payload)).decode()

    def decrypt(self, ciphertext: str) -> dict[str, str]:
        if not ciphertext:
            return {}
        if self._box is None:
            raise SecretVaultError(
                "The credential vault is locked. Configure WORKSPACE_SECRET_KEY first."
            )
        try:
            payload = self._box.decrypt(base64.urlsafe_b64decode(ciphertext.encode()))
            decoded = json.loads(payload)
        except (CryptoError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SecretVaultError(
                "Saved credentials could not be decrypted with the current workspace key."
            ) from exc
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in decoded.items()
        ):
            raise SecretVaultError("Saved credentials have an invalid format.")
        return decoded

    def merge(
        self,
        ciphertext: str,
        updates: dict[str, str],
    ) -> tuple[str, list[str]]:
        existing = self.decrypt(ciphertext) if ciphertext else {}
        existing.update(
            {key: value.strip() for key, value in updates.items() if value.strip()}
        )
        return self.encrypt(existing), sorted(existing)
