"""Authentication as a Secure Service Account (SSA): a robot user of a Server-to-Server APS app.

The robot signs a short-lived JWT with its private key and exchanges it for a three-legged access token
(user context, one hour). There is no browser login and no refresh token: when a token expires, a new one
is requested the same way. All credentials are static, so they can live in plain environment variables.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from authlib.integrations.httpx_client import AssertionClient
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

from fusion_backup.cloud import CloudError

TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"
_SCOPES = ("data:read",)
_ASSERTION_LIFETIME_SECONDS = 300  # as in Autodesk's SSA examples

_ENV_CLIENT_ID = "APS_CLIENT_ID"
_ENV_CLIENT_SECRET = "APS_CLIENT_SECRET"
_ENV_SSA_ID = "APS_SSA_ID"
_ENV_SSA_KEY_ID = "APS_SSA_KEY_ID"
_ENV_SSA_PRIVATE_KEY = "APS_SSA_PRIVATE_KEY"
_ENV_SSA_PRIVATE_KEY_FILE = "APS_SSA_PRIVATE_KEY_FILE"


class AuthenticationError(CloudError):
    """The robot's credentials are missing or were rejected."""


@dataclass(frozen=True)
class SsaCredentials:
    """Everything needed to act as the robot. Secrets are kept out of `repr`, so they never end up in logs."""

    client_id: str
    client_secret: str = field(repr=False)
    service_account_id: str
    key_id: str
    private_key: str = field(repr=False)  # PEM

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SsaCredentials:
        """Read the credentials from environment variables (default: the process environment).

        The private key comes from APS_SSA_PRIVATE_KEY (the PEM text, e.g. a CI secret) or from the file
        named in APS_SSA_PRIVATE_KEY_FILE (for local use).
        """
        env = os.environ if env is None else env
        missing = [name for name in (_ENV_CLIENT_ID, _ENV_CLIENT_SECRET, _ENV_SSA_ID, _ENV_SSA_KEY_ID) if not env.get(name)]
        private_key = env.get(_ENV_SSA_PRIVATE_KEY, "")
        key_file = env.get(_ENV_SSA_PRIVATE_KEY_FILE)
        if not private_key and key_file:
            try:
                private_key = Path(key_file).expanduser().read_text()
            except OSError as err:
                raise AuthenticationError(f"Cannot read {_ENV_SSA_PRIVATE_KEY_FILE} ({key_file}): {err.strerror}") from err
        if not private_key:
            missing.append(f"{_ENV_SSA_PRIVATE_KEY} or {_ENV_SSA_PRIVATE_KEY_FILE}")
        if missing:
            raise AuthenticationError(f"Missing environment variables: {', '.join(missing)}")
        return cls(env[_ENV_CLIENT_ID], env[_ENV_CLIENT_SECRET], env[_ENV_SSA_ID], env[_ENV_SSA_KEY_ID], private_key)


class _SsaClient(AssertionClient):
    """Authlib's JWT-bearer client (RFC 7523), adjusted to how APS expects the assertion."""

    def refresh_token(self) -> dict[str, Any]:
        # Authlib stores the generated `jti` in the claims dict it is given; a fresh dict per token request
        # keeps every assertion's ID unique. APS expects the scopes as a list inside the assertion.
        self.claims = {"scope": list(_SCOPES)}
        token = super().refresh_token()
        if not token or "access_token" not in token:
            raise AuthenticationError("Autodesk answered the robot login without an access token")
        return token


def connect(credentials: SsaCredentials, *, transport: httpx2.BaseTransport | None = None, timeout: float = 300) -> AssertionClient:
    """An HTTP client logged in as the robot; it requests a new access token whenever the current one expires.

    Requests to pre-signed URLs must pass `auth=None`, so they carry neither the token nor the client secret.
    """
    try:
        key = RSAKey.import_key(credentials.private_key)
    except (JoseError, ValueError) as err:
        raise AuthenticationError("The robot's private key is not a valid RSA private key in PEM format") from err
    client = _SsaClient(
        token_endpoint=TOKEN_URL,
        issuer=credentials.client_id,
        subject=credentials.service_account_id,
        audience=TOKEN_URL,
        scope=" ".join(_SCOPES),
        key=key,
        header={"alg": "RS256", "kid": credentials.key_id},
        expires_in=_ASSERTION_LIFETIME_SECONDS,
        transport=transport,
        timeout=timeout,
    )
    # The token request authenticates the app with HTTP Basic; API requests replace this with the bearer token.
    client.auth = httpx2.BasicAuth(credentials.client_id, credentials.client_secret)
    return client
