# Type stubs for the part of Authlib that fusion-backup uses (Authlib ships no type hints).

class OAuthError(Exception):
    error: str | None
    description: str | None
    def __init__(self, error: str | None = None, description: str | None = None, uri: str | None = None) -> None: ...
