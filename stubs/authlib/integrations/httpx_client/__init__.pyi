# Type stubs for the part of Authlib that fusion-backup uses (Authlib ships no type hints).

from typing import Any

import httpx2

class AssertionClient(httpx2.Client):
    """JWT bearer grant client (RFC 7523): signs an assertion and exchanges it for an access token."""

    claims: dict[str, Any] | None
    def __init__(
        self,
        token_endpoint: str,
        issuer: str,
        subject: str,
        audience: str | None = None,
        grant_type: str | None = None,
        claims: dict[str, Any] | None = None,
        token_placement: str = "header",
        scope: str | None = None,
        client_id: str | None = None,
        **kwargs: Any,
    ) -> None: ...
    def refresh_token(self) -> dict[str, Any] | None: ...
    def request(
        self, method: str, url: httpx2.URL | str, *, withhold_token: bool = False, auth: Any = ..., **kwargs: Any
    ) -> httpx2.Response: ...
