"""Tests for POST /billing/google/verify (Subtask 5 — Pro activation).

The route verifies a Play purchase token via the injectable
``_verify_google_purchase`` seam and flips the authenticated player's
plan through ``entitlements.set_plan`` only on an entitled verdict.
Pinned here:

1.  Valid token (fake verdict) → 200 with the documented body and
    ``players.plan`` flipped to ``"pro"``; the seam receives exactly
    the request's token + product.
2.  Not-entitled verdict → 402, plan stays ``"free"``.
3.  Unknown ``product_id`` → 400 BEFORE any Google call (the seam is
    never invoked).
4.  Upstream failure → 502; unconfigured credentials → 503 (both leave
    the plan unchanged).  The REAL seam raises
    ``BillingNotConfiguredError`` when the GOOGLE_PLAY_* env vars are
    absent — no fake success on unconfigured deploys.
5.  DB failure during the flip re-raises after rollback (surfaces as a
    500) and the plan does NOT change — a 200 is never a fake success.
6.  Owner-scoped: the flip lands on the caller only.
7.  HTTP-layer auth: no Bearer → 401 (via the real app, TestClient).
8.  Contract doc pin: §36 exists and documents ``pro_monthly``.

Direct endpoint-call style for 1–6 (limiter disabled, fake request);
TestClient only for the auth-layer test.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

os.environ.setdefault("SECA_API_KEY", "ci-test-key")
os.environ.setdefault("SECA_ENV", "dev")
os.environ.setdefault("SECRET_KEY", "ci-secret-key-that-is-32-chars-long!!")

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

import llm.seca.billing.router as billing_router
from llm.seca.auth.models import Base, Player
from llm.seca.billing.router import (
    BillingNotConfiguredError,
    BillingUpstreamError,
    PurchaseVerdict,
    VerifyGooglePurchaseRequest,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_request() -> StarletteRequest:
    return StarletteRequest({
        "type": "http", "method": "POST", "path": "/billing/google/verify",
        "headers": [], "client": ("127.0.0.1", 0),
    })


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_player(db, email: str) -> Player:
    p = Player(email=email, password_hash="not-used-here")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def player(db):
    return _make_player(db, "buyer@test.com")


class _VerifierSpy:
    def __init__(self, verdict: PurchaseVerdict | None = None, raise_exc: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.verdict = verdict
        self.raise_exc = raise_exc

    def __call__(self, purchase_token: str, product_id: str) -> PurchaseVerdict:
        self.calls.append((purchase_token, product_id))
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.verdict is not None
        return self.verdict


@pytest.fixture()
def no_limiter(monkeypatch):
    from llm.seca.shared_limiter import limiter

    monkeypatch.setattr(limiter, "enabled", False)


def _call(db, player, token: str = "tok-abc123", product: str = "pro_monthly"):
    req = VerifyGooglePurchaseRequest(purchase_token=token, product_id=product)
    return asyncio.run(
        billing_router.verify_google_purchase(
            req=req, request=_fake_request(), player=player, db=db
        )
    )


# ---------------------------------------------------------------------------
# 1 + 6. Happy path
# ---------------------------------------------------------------------------


class TestValidPurchase:
    def test_flips_to_pro_and_returns_documented_body(self, db, player, no_limiter, monkeypatch):
        spy = _VerifierSpy(PurchaseVerdict(entitled=True, state="SUBSCRIPTION_STATE_ACTIVE"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        response = _call(db, player, token="tok-live-1")

        assert response == {
            "plan": "pro",
            "product_id": "pro_monthly",
            "state": "SUBSCRIPTION_STATE_ACTIVE",
        }
        assert spy.calls == [("tok-live-1", "pro_monthly")], (
            "the seam must receive exactly the request's token + product"
        )
        db.refresh(player)
        assert player.plan == "pro"

    def test_owner_scoped_flip(self, db, player, no_limiter, monkeypatch):
        bystander = _make_player(db, "bystander@test.com")
        spy = _VerifierSpy(PurchaseVerdict(entitled=True, state="SUBSCRIPTION_STATE_ACTIVE"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        _call(db, player)

        db.refresh(player)
        db.refresh(bystander)
        assert player.plan == "pro"
        assert bystander.plan == "free", "only the authenticated caller's plan may flip"

    def test_canceled_but_unexpired_is_entitled(self, db, player, no_limiter, monkeypatch):
        """subscriptionsv2 CANCELED = auto-renew off, paid period still
        running — access holds until EXPIRED (see router docstring)."""
        spy = _VerifierSpy(PurchaseVerdict(entitled=True, state="SUBSCRIPTION_STATE_CANCELED"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        response = _call(db, player)
        assert response["plan"] == "pro"


# ---------------------------------------------------------------------------
# 2 + 3 + 4. Rejection paths — plan never flips
# ---------------------------------------------------------------------------


class TestRejections:
    def _assert_plan_unchanged(self, db, player):
        db.refresh(player)
        assert player.plan == "free"

    def test_not_entitled_verdict_402(self, db, player, no_limiter, monkeypatch):
        spy = _VerifierSpy(PurchaseVerdict(entitled=False, state="SUBSCRIPTION_STATE_EXPIRED"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        with pytest.raises(HTTPException) as exc:
            _call(db, player)
        assert exc.value.status_code == 402
        assert "SUBSCRIPTION_STATE_EXPIRED" in exc.value.detail
        self._assert_plan_unchanged(db, player)

    def test_unknown_product_400_without_google_call(self, db, player, no_limiter, monkeypatch):
        spy = _VerifierSpy(PurchaseVerdict(entitled=True, state="SUBSCRIPTION_STATE_ACTIVE"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        with pytest.raises(HTTPException) as exc:
            _call(db, player, product="gold_yearly")
        assert exc.value.status_code == 400
        assert spy.calls == [], "unknown products must be rejected before any Google call"
        self._assert_plan_unchanged(db, player)

    def test_upstream_error_502(self, db, player, no_limiter, monkeypatch):
        spy = _VerifierSpy(raise_exc=BillingUpstreamError("Play API answered HTTP 500"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        with pytest.raises(HTTPException) as exc:
            _call(db, player)
        assert exc.value.status_code == 502
        self._assert_plan_unchanged(db, player)

    def test_not_configured_503(self, db, player, no_limiter, monkeypatch):
        spy = _VerifierSpy(raise_exc=BillingNotConfiguredError("env unset"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        with pytest.raises(HTTPException) as exc:
            _call(db, player)
        assert exc.value.status_code == 503
        self._assert_plan_unchanged(db, player)

    def test_real_seam_raises_not_configured_without_env(self, monkeypatch):
        """The REAL verifier (no fake) must refuse loudly when the
        GOOGLE_PLAY_* vars are absent — never fake-verify."""
        for var in (
            "GOOGLE_PLAY_PACKAGE_NAME",
            "GOOGLE_PLAY_SA_EMAIL",
            "GOOGLE_PLAY_SA_PRIVATE_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(BillingNotConfiguredError):
            billing_router._verify_google_purchase("tok", "pro_monthly")


# ---------------------------------------------------------------------------
# 5. Persistence failure — no fake success
# ---------------------------------------------------------------------------


class TestPersistenceFailure:
    def test_db_failure_reraises_and_plan_unchanged(self, db, player, no_limiter, monkeypatch):
        spy = _VerifierSpy(PurchaseVerdict(entitled=True, state="SUBSCRIPTION_STATE_ACTIVE"))
        monkeypatch.setattr(billing_router, "_verify_google_purchase", spy)

        def _boom():
            raise SQLAlchemyError("forced commit failure")

        commit_patch = pytest.MonkeyPatch()
        commit_patch.setattr(db, "commit", _boom)
        try:
            with pytest.raises(SQLAlchemyError):
                _call(db, player)
        finally:
            commit_patch.undo()

        db.rollback()
        db.refresh(player)
        assert player.plan == "free", "a failed flip must not persist (rollback + re-raise)"


# ---------------------------------------------------------------------------
# 7. HTTP-layer auth (real app)
# ---------------------------------------------------------------------------


class TestHttpAuth:
    def test_missing_bearer_is_401(self):
        from fastapi.testclient import TestClient

        import llm.server as server

        client = TestClient(server.app)
        response = client.post(
            "/billing/google/verify",
            json={"purchase_token": "tok", "product_id": "pro_monthly"},
            headers={"X-Api-Key": os.environ.get("SECA_API_KEY", "ci-test-key")},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 8. Contract doc pin
# ---------------------------------------------------------------------------


class TestContractDocumented:
    def test_api_contracts_documents_the_endpoint(self):
        doc = (_REPO_ROOT / "docs" / "API_CONTRACTS.md").read_text(encoding="utf-8")
        assert "## 36. `POST /billing/google/verify`" in doc
        assert "pro_monthly" in doc
        assert "purchase verification not configured" in doc
