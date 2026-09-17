import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.parsers.document import ParseFailure, parse
from app.main import create_app
from app.modules.identity.service import verify_oidc


def test_oidc_signature_issuer_audience_expiry_nonce_and_mapping(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cfg = Settings(
        _env_file=None,
        identity_provider="oidc",
        oidc_issuer="https://issuer.example",
        oidc_client_id="clinical",
        oidc_jwks_url="https://issuer.example/jwks",
    )
    monkeypatch.setattr(
        jwt.PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=private.public_key()),
    )
    monkeypatch.setattr("app.modules.identity.service.mapped_user", lambda *args: {"id": uuid4()})
    claims = {
        "iss": cfg.oidc_issuer,
        "sub": "test-user",
        "aud": cfg.oidc_client_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "expected-nonce",
    }
    token = jwt.encode(claims, private, algorithm="RS256")
    assert verify_oidc(cfg, token, "expected-nonce")["id"]
    for update in (
        {"iss": "https://other.invalid"},
        {"aud": "another-client"},
        {"exp": 1},
        {"nonce": "wrong"},
        {"azp": "wrong"},
    ):
        bad = jwt.encode({**claims, **update}, private, algorithm="RS256")
        with pytest.raises(HTTPException) as exc:
            verify_oidc(cfg, bad, "expected-nonce")
        assert exc.value.status_code == 401
    unsigned = jwt.encode(claims, "", algorithm="none")
    with pytest.raises(HTTPException):
        verify_oidc(cfg, unsigned, "expected-nonce")

    def forbidden(*args):
        raise HTTPException(403)

    monkeypatch.setattr("app.modules.identity.service.mapped_user", forbidden)
    with pytest.raises(HTTPException) as exc:
        verify_oidc(cfg, token, "expected-nonce")
    assert exc.value.status_code == 403


def test_oidc_pkce_and_production_synthetic_is_disabled():
    cfg = Settings(
        _env_file=None,
        identity_provider="oidc",
        oidc_client_id="clinical",
        oidc_authorization_url="https://issuer.example/authorize",
    )
    client = TestClient(create_app(cfg))
    response = client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert "code_challenge_method=S256" in response.headers["location"]
    assert "nonce=" in response.headers["location"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert (
        client.post("/api/v1/auth/synthetic", json={"access_code": "anything"}).status_code == 404
    )
    for changes in ({"identity_provider": "synthetic"}, {"identity_provider": "oidc"}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, app_env="production", **changes)


def test_encrypted_pdf_page_limits_and_source_offsets(tmp_path):
    import pymupdf

    with pymupdf.open() as doc:
        doc.new_page()
        doc.new_page()
        plain = doc.tobytes()
        encrypted = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user"
        )
    with pytest.raises(ParseFailure, match="PAGE_LIMIT"):
        parse(plain, "application/pdf", tmp_path, 1, 100_000)
    with pytest.raises(ParseFailure, match="ENCRYPTED_PDF"):
        parse(encrypted, "application/pdf", tmp_path, 100, 100_000)
    result = parse("合成😀\r\n未知".encode(), "text/plain", tmp_path, 100, 100_000)
    block = result["pages"][0]["blocks"][0]
    assert result["text"][block["start"] : block["end"]] == block["text"]
    assert block["end"] == 7
