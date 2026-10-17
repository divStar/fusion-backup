"""Thin HTTP layer over the logged-in client: parses answers into DTOs and turns every transport, HTTP and
login failure into a `CloudError` with a readable message. Pre-signed URLs and Autodesk's raw answers never
appear in those messages, because both can contain credentials."""

from typing import Any

import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AssertionClient
from pydantic import BaseModel, ValidationError

from fusion_backup.aps._dto import GraphQLError
from fusion_backup.aps.auth import AuthenticationError
from fusion_backup.cloud import CloudError

_API = "https://developer.api.autodesk.com"


class ApsRequestError(CloudError):
    """An APS request was answered with an error status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ApsClient:
    """Sends authenticated requests to APS."""

    def __init__(self, http: AssertionClient):
        self._http = http

    def get[T: BaseModel](self, path_or_url: str, model: type[T]) -> T:
        """GET an APS endpoint (path below the API root, or a full URL) and parse the answer."""
        url = path_or_url if path_or_url.startswith("https://") else _API + path_or_url
        return _parse(model, self._send("GET", url).content)

    def graphql[T: BaseModel](self, path: str, query: str, variables: dict[str, Any], model: type[T]) -> T:
        """Run a GraphQL query against the endpoint at `path` (below the API root) and parse its `data`; GraphQL errors raise."""
        answer = self._send("POST", _API + path, json={"query": query, "variables": variables}).json()
        try:
            if answer.get("errors"):
                messages = "; ".join(GraphQLError.model_validate(error).message for error in answer["errors"])
                raise CloudError(f"GraphQL error: {messages}")
            return model.model_validate(answer["data"])
        except ValidationError as err:
            raise CloudError(f"Unexpected GraphQL answer from Autodesk ({model.__name__}): {_describe(err)}") from err

    def download(self, signed_url: str) -> bytes:
        """Fetch a pre-signed URL (S3 or CDN). It carries its own credentials, so no other auth is sent."""
        return self._send("GET", signed_url, auth=None, signed=True).content

    def _send(self, method: str, url: str, *, signed: bool = False, **kwargs: Any) -> httpx2.Response:
        public_url = url.split("?", 1)[0]  # pre-signed URLs carry credentials in the query string
        try:
            response = self._http.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx2.HTTPStatusError as err:
            status = err.response.status_code
            # storage services may echo the signed request in their error body, so it is only shown for APS endpoints
            detail = "" if signed else f": {err.response.text[:500]}"
            raise ApsRequestError(status, f"HTTP {status} for {public_url}{detail}") from err
        except httpx2.HTTPError as err:
            raise CloudError(f"Request to {public_url} failed: {type(err).__name__}") from err
        except OAuthError as err:
            raise AuthenticationError(f"Autodesk rejected the robot login ({err.error}: {err.description})") from err


def _parse[T: BaseModel](model: type[T], content: bytes) -> T:
    try:
        return model.model_validate_json(content)
    except ValidationError as err:
        raise CloudError(f"Unexpected answer from Autodesk ({model.__name__}): {_describe(err)}") from err


def _describe(err: ValidationError) -> str:
    """Field paths and reasons only; the offending values are left out, as they may be signed URLs."""
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}" for e in err.errors(include_url=False, include_input=False))
