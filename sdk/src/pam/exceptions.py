"""SDK exceptions — transport/programming errors only."""

from __future__ import annotations

from typing import Any


class PAMError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PAMAuthError(PAMError):
    """HTTP 401 — invalid or missing API key."""


class PAMTimeoutError(PAMError):
    """Client timeout exceeded."""


class PAMAPIError(PAMError):
    """HTTP 5xx or malformed server response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.body = body
