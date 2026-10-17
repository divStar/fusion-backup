import base64
import urllib.parse
from pathlib import Path

import httpx2
import pytest
from fakes import ScriptedTransport, robot_credentials, robot_env, robot_key, token_answer
from joserfc import jwt

from fusion_backup.aps.auth import TOKEN_URL, AuthenticationError, SsaCredentials, connect
from fusion_backup.aps.client import ApsClient
from fusion_backup.aps.data_management import DataManagementApi

API = "https://developer.api.autodesk.com"


def form(request: httpx2.Request) -> dict[str, str]:
    return {key: values[0] for key, values in urllib.parse.parse_qs(request.content.decode()).items()}


def test_credentials_from_env() -> None:
    credentials = SsaCredentials.from_env(robot_env())
    assert credentials == robot_credentials()
    assert "secret" not in repr(credentials) and "PRIVATE KEY" not in repr(credentials)


def test_private_key_can_come_from_a_file(tmp_path: Path) -> None:
    key_file = tmp_path / "robot.pem"
    key_file.write_text(robot_credentials().private_key)
    env = {**robot_env(), "APS_SSA_PRIVATE_KEY": "", "APS_SSA_PRIVATE_KEY_FILE": str(key_file)}
    assert SsaCredentials.from_env(env).private_key == robot_credentials().private_key
    with pytest.raises(AuthenticationError, match="Cannot read APS_SSA_PRIVATE_KEY_FILE"):
        SsaCredentials.from_env({**env, "APS_SSA_PRIVATE_KEY_FILE": str(tmp_path / "missing.pem")})


def test_missing_credentials_are_listed() -> None:
    with pytest.raises(AuthenticationError) as caught:
        SsaCredentials.from_env({"APS_CLIENT_ID": "client"})
    message = str(caught.value)
    assert "APS_CLIENT_SECRET" in message and "APS_SSA_ID" in message and "APS_SSA_KEY_ID" in message
    assert "APS_SSA_PRIVATE_KEY or APS_SSA_PRIVATE_KEY_FILE" in message and "APS_CLIENT_ID," not in message


def test_token_request_signs_a_jwt_assertion_and_authenticates_the_app() -> None:
    transport = ScriptedTransport().add("POST", TOKEN_URL, token_answer()).add("GET", f"{API}/x", {"ok": True})
    client = connect(robot_credentials(), transport=transport)

    assert client.get(f"{API}/x").json() == {"ok": True}
    token_request, api_request = transport.requests
    sent = form(token_request)
    assert sent["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer" and sent["scope"] == "data:read"
    assert token_request.headers["Authorization"] == "Basic " + base64.b64encode(b"client:secret").decode()

    assertion = jwt.decode(sent["assertion"], robot_key())  # verifies the signature
    assert (assertion.header["alg"], assertion.header["kid"]) == ("RS256", "kid-1")
    claims = assertion.claims
    assert (claims["iss"], claims["sub"], claims["aud"], claims["scope"]) == ("client", "SA123", TOKEN_URL, ["data:read"])
    assert 0 < claims["exp"] - claims["iat"] <= 300
    assert api_request.headers["Authorization"] == "Bearer access-1"


def test_a_new_token_is_requested_when_it_expires_with_a_fresh_jwt_id() -> None:
    transport = (
        ScriptedTransport()
        .add("POST", TOKEN_URL, token_answer(1, expires_in=30))  # within Authlib's 60 s leeway: expired at once
        .add("GET", f"{API}/a", {})
        .add("POST", TOKEN_URL, token_answer(2))
        .add("GET", f"{API}/b", {})
        .add("GET", f"{API}/c", {})
    )
    client = connect(robot_credentials(), transport=transport)
    for path in ("a", "b", "c"):
        client.get(f"{API}/{path}")
    token_requests = [request for request in transport.requests if str(request.url) == TOKEN_URL]
    assert len(token_requests) == 2  # the second token is reused for /c
    jwt_ids = {jwt.decode(form(request)["assertion"], robot_key()).claims["jti"] for request in token_requests}
    assert len(jwt_ids) == 2
    assert transport.requests[-1].headers["Authorization"] == "Bearer access-2"


def test_invalid_private_key() -> None:
    credentials = SsaCredentials("client", "secret", "SA123", "kid-1", "not a key")
    with pytest.raises(AuthenticationError, match="not a valid RSA private key"):
        connect(credentials)


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (httpx2.Response(400, json={"error": "invalid_grant", "error_description": "bad assertion"}), "invalid_grant: bad assertion"),
        ({"token_type": "Bearer"}, "without an access token"),
    ],
)
def test_rejected_robot_login(answer: httpx2.Response | dict[str, str], message: str) -> None:
    transport = ScriptedTransport().add("POST", TOKEN_URL, answer)
    api = DataManagementApi(ApsClient(connect(robot_credentials(), transport=transport)))
    with pytest.raises(AuthenticationError, match=message):
        api.hubs()
