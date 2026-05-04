"""Unit tests for auth/tokens.py: JWT creation, decoding, expiry, and tampering."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from llm.seca.auth.tokens import (
    ACCESS_EXPIRE_MINUTES,
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    decode_token,
)


class TestCreateAccessToken:
    def test_returns_string(self):
        token = create_access_token("player-1", "session-1")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_payload_contains_player_id(self):
        token = create_access_token("player-abc", "session-xyz")
        payload = decode_token(token)
        assert payload["player_id"] == "player-abc"

    def test_payload_contains_session_id(self):
        token = create_access_token("player-abc", "session-xyz")
        payload = decode_token(token)
        assert payload["session_id"] == "session-xyz"

    def test_payload_contains_exp_claim(self):
        token = create_access_token("p1", "s1")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert "exp" in payload

    def test_expiry_is_approximately_access_expire_minutes(self):
        before = datetime.utcnow()
        token = create_access_token("p1", "s1")
        after = datetime.utcnow()

        raw = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        exp = datetime.utcfromtimestamp(raw["exp"])

        lower = before + timedelta(minutes=ACCESS_EXPIRE_MINUTES - 1)
        upper = after + timedelta(minutes=ACCESS_EXPIRE_MINUTES + 1)
        assert lower <= exp <= upper


class TestDecodeToken:
    def test_valid_token_decodes_correctly(self):
        token = create_access_token("player-1", "session-1")
        payload = decode_token(token)
        assert payload["player_id"] == "player-1"
        assert payload["session_id"] == "session-1"

    def test_expired_token_raises(self):
        payload = {
            "player_id": "p1",
            "session_id": "s1",
            "exp": datetime.utcnow() - timedelta(seconds=1),
        }
        expired_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(expired_token)

    def test_tampered_signature_raises(self):
        token = create_access_token("p1", "s1")
        parts = token.split(".")
        tampered = parts[0] + "." + parts[1] + ".invalidsignature"
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(tampered)

    def test_wrong_secret_raises(self):
        token = jwt.encode(
            {"player_id": "p1", "session_id": "s1"},
            "wrong-secret-that-is-at-least-32-characters-long",
            algorithm=ALGORITHM,
        )
        with pytest.raises(jwt.InvalidTokenError):
            decode_token(token)

    def test_malformed_token_raises(self):
        with pytest.raises(jwt.InvalidTokenError):
            decode_token("not.a.valid.jwt.token.at.all")


class TestSecretKeyConstraints:
    def test_secret_key_is_at_least_32_chars(self):
        assert len(SECRET_KEY) >= 32, (
            f"SECRET_KEY must be at least 32 characters, got {len(SECRET_KEY)}"
        )
