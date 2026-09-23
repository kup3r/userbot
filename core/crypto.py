"""Encryption helpers for tenant session strings."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                "SESSION_ENCRYPTION_KEY is not a valid Fernet key. "
                "Generate one with: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(
                "Cannot decrypt a stored session. "
                "Check that SESSION_ENCRYPTION_KEY has not changed."
            ) from exc
