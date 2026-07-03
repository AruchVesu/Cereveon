"""Pydantic request schemas for the FastAPI server in ``llm/server.py``.

Extracted from ``server.py`` in the 2026-05-24 size-reduction cleanup
(item #6 of the technical-debt survey).  ``server.py`` re-exports every
symbol from this module so existing ``from llm.server import X``
imports across the codebase (≈ 68 sites at extraction time) continue
to work without churn.

Cohesion rationale
------------------
All eleven request models share the same dependency surface (pydantic
+ chess + ``sanitize_user_query``) and the same validator helper
(``_validate_fen_field``).  Routes that consume them live in
``server.py`` and import them via re-export; route logic + middleware
+ lifespan stay in ``server.py`` to avoid splitting the FastAPI app
across modules in this commit.  Further splitting (route submodules)
is the broader Option B from the survey; this is Option A.

Trust-boundary note
-------------------
Several of these models — most prominently ``AnalyzeRequest`` — are
the trust boundary between client-supplied data and the engine pool /
LLM pipeline.  When tightening a validator, check the consuming route
in ``server.py`` and any boundary-side re-validation in
``llm/rag/validators/explain_response_schema.py``.  Architectural
invariants pinned by ``test_architectural_invariants.py`` and
``test_api_contract_validation.py``.
"""

from __future__ import annotations

import re
from typing import Literal

import chess
from pydantic import BaseModel, ValidationInfo, field_validator

from llm.rag.prompts.input_sanitizer import sanitize_user_query


def _validate_fen_field(v: str) -> str:
    stripped = v.strip()
    if stripped.lower() == "startpos":
        return v
    parts = stripped.split()
    if len(parts) != 6 or len(stripped) > 100:
        raise ValueError("invalid FEN")
    try:
        chess.Board(stripped)
    except ValueError:
        raise ValueError("invalid FEN")
    return v


class AnalyzeRequest(BaseModel):
    """Request shape for ``/analyze`` and ``/explain``.

    Pre-PR-9 this carried an optional ``stockfish_json: dict`` field
    that the route handlers piped directly into ``extract_engine_signal``.
    That was a trust-boundary inconsistency with the architecture
    invariant *"Stockfish JSON: Trusted"* (which implies the JSON is
    server-authentic, not client-supplied).  A modded client could
    claim any position evaluation and the server would build an ESV
    from it.  Practical impact was bounded (``/explain`` is
    SafeExplainer-only, no LLM gating; ``/analyze`` just returns the
    ESV to the caller — both deceiving only the client itself), but
    the architectural inconsistency was real.

    PR 9 removes the field.  Pydantic's default extra-field policy
    (``ignore``) silently drops any ``stockfish_json`` a back-compat
    client still sends, so this is not a breaking change for the
    sending side; only the server's access to that value is gone.
    Both handlers now build the ESV from FEN-only heuristics via
    ``extract_engine_signal(None, fen=req.fen)`` — the same fall-back
    path that already handled missing/empty stockfish_json.

    Routes that need real engine evaluation (``/live/move``,
    ``/seca/explain``) acquire the engine pool server-side and feed
    extract_engine_signal with the authentic Stockfish output — that
    path is unchanged.
    """

    fen: str
    user_query: str | None = ""

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("user_query")
    @classmethod
    def validate_user_query(cls, v: str | None) -> str | None:
        if v and len(v) > 2000:
            raise ValueError("user_query too long (max 2000 chars)")
        return sanitize_user_query(v) if v else v


class EngineEvalRequest(BaseModel):
    """Body of POST /engine/eval — Android's HttpEngineEvalClient sends
    only a FEN.  Previously hosted by the standalone host_app.py debug
    server (never deployed to production, so the Android calls 404'd
    silently and the eval-after-AI-move badge in MainActivity rendered
    "⚠ Eval N/A" until this route was migrated to server.py).

    Contract intentionally narrower than host_app's: drops the unused
    GET variant + ``moves``/``movetime_ms``/``nodes`` fields that no
    in-tree caller sends.  Adding them back is a contract widening
    that requires an Android client update in the same release.
    """

    fen: str

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)


class LiveMoveRequest(BaseModel):
    fen: str
    uci: str
    player_id: str | None = None
    # Optional position BEFORE the move (the move quality is graded by the
    # eval swing fen_before -> fen, which the server can't reconstruct from the
    # post-move FEN alone — a capture/en-passant/promotion loses the captured
    # piece).  Absent (older clients) -> move quality stays "unknown", the
    # pre-feature behaviour.  Validated through the same FEN gate as ``fen``.
    fen_before: str | None = None

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("fen_before")
    @classmethod
    def validate_fen_before(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_fen_field(v)

    @field_validator("uci")
    @classmethod
    def validate_uci(cls, v: str) -> str:
        if not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbnQRBN]?", v):
            raise ValueError(
                "uci move must be [a-h][1-8][a-h][1-8] with optional promotion [qrbnQRBN]"
            )
        return v

    @field_validator("player_id")
    @classmethod
    def validate_player_id(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 100:
            raise ValueError("player_id too long (max 100 chars)")
        return v

    # Distinct-game key (``games.id``) for the entitlements admission
    # check — the free tier's "N LLM-coached games per day" metering
    # counts distinct game_ids, not moves.  Same posture and validator
    # as ``ChatRequest.game_id``: optional, ≤64 chars, empty → None.
    # Absent → the admission check fails OPEN (older clients that don't
    # send it are never degraded).  Additive + backward-compatible.
    game_id: str | None = None

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if len(v) > 64:
            raise ValueError("game_id too long (max 64 chars)")
        return v


class StartGameRequest(BaseModel):
    # T3: player_id is now derived from the authenticated session.  The field
    # is accepted (optional) for backwards compatibility with older Android
    # clients that still send it, and ignored server-side.  Remove the field
    # once all clients have been updated to omit it.
    player_id: str | None = None

    @field_validator("player_id")
    @classmethod
    def validate_player_id(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 100:
            raise ValueError("player_id too long (max 100 chars)")
        return v


class CurriculumRecommendRequest(BaseModel):
    skill_vector: list[float]


class GameRequest(BaseModel):
    player_id: str
    pgn: str


class GameFinishRequest(BaseModel):
    player_id: str
    pgn: str


class GameFinishClosedLoopRequest(BaseModel):
    player_id: int
    game_id: int


class ChatTurnModel(BaseModel):
    """A single turn in a coaching conversation."""

    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str, info: ValidationInfo) -> str:
        # 2000-char cap applies to EVERY turn (both roles), unconditionally.
        if len(v) > 2000:
            raise ValueError("message content too long (max 2000 chars)")
        if not v:
            return v
        # Only the coach's OWN assistant turns are trusted server output
        # (already passed Mode-2 OUTPUT validation when generated). USER turns
        # — and anything NOT explicitly an assistant turn — are untrusted and
        # must pass the prompt-injection screen. Screening the coach's own
        # replies false-positives on legitimate coaching prose and 422s the
        # whole /chat request (the client shows the silent "Coach is offline").
        #
        # Fail CLOSED: a missing/unknown role (role validation failed, or a
        # future third role) is SCREENED, not trusted. `role` is validated
        # before `content` (declaration order, pinned by a field-order test),
        # so it is available via info.data here.
        #
        # TODO(durable fix): reconstruct assistant history from the server-
        # authoritative chat_turns store keyed on the authenticated player and
        # ignore client-supplied assistant content entirely — then assistant
        # turns are trusted because they came from the server, closing the
        # forged-assistant-turn surface this flag leaves open.
        role = info.data.get("role")
        return sanitize_user_query(v, reject_injection=(role != "assistant"))


class ChatRequest(BaseModel):
    """Request body for POST /chat."""

    fen: str
    messages: list[ChatTurnModel]
    player_profile: dict | None = None
    past_mistakes: list[str] | None = None
    # Coach voice setting from the Android Settings sheet — affects
    # the LLM's tone but never its content (the engine signal stays
    # authoritative).  Strict allow-list so an unknown value fails
    # validation rather than silently bleeding into the prompt.
    coach_voice: str | None = None

    @field_validator("player_profile")
    @classmethod
    def validate_player_profile(cls, v: dict | None) -> dict | None:
        if v is not None:
            if len(v) > 20:
                raise ValueError("player_profile too many keys (max 20)")
            total = sum(len(str(k)) + len(str(val)) for k, val in v.items())
            if total > 2000:
                raise ValueError("player_profile too large (max 2000 chars total)")
            for k, val in v.items():
                if isinstance(k, str):
                    sanitize_user_query(k)
                if isinstance(val, str):
                    sanitize_user_query(val)
        return v

    move_count: int | None = None
    # The player's last move (UCI), so the coach can describe it in plain
    # English ("you advanced your f-pawn") instead of misreading the raw FEN.
    # Optional: older clients omit it; the prompt simply skips the move line.
    last_move: str | None = None

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("last_move")
    @classmethod
    def validate_last_move(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbnQRBN]?", v):
            raise ValueError(
                "last_move must be UCI [a-h][1-8][a-h][1-8] with optional promotion [qrbnQRBN]"
            )
        return v

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v: list) -> list:
        if len(v) > 50:
            raise ValueError("too many messages in history (max 50)")
        return v

    @field_validator("past_mistakes")
    @classmethod
    def validate_past_mistakes(cls, v: list | None) -> list | None:
        if v is not None:
            if len(v) > 20:
                raise ValueError("past_mistakes list too long (max 20)")
            for item in v:
                if len(item) > 500:
                    raise ValueError("past_mistakes item too long (max 500 chars)")
                sanitize_user_query(item)
        return v

    @field_validator("move_count")
    @classmethod
    def validate_move_count(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 10_000):
            raise ValueError("move_count must be 0–10000")
        return v

    @field_validator("coach_voice")
    @classmethod
    def validate_coach_voice(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v == "":
            return None
        # Allow-list mirrors the Android SettingsBottomSheet radio
        # values exactly.  Any other value (e.g. attacker-supplied
        # prompt-injection bait disguised as a tone) is rejected
        # before it reaches the LLM prompt.
        if v not in {"formal", "conversational", "terse"}:
            raise ValueError("coach_voice must be one of 'formal', 'conversational', 'terse'")
        return v

    # Per-game chat thread key (``games.id``). When present, the exchange is
    # saved under this game so chat history is scoped per game; absent/null
    # keeps it player-global (legacy). ``player_id`` (from the JWT) remains the
    # isolation boundary, so this is only an organizational key.
    game_id: str | None = None

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if len(v) > 64:
            raise ValueError("game_id too long (max 64 chars)")
        return v


class GameCheckpointRequest(BaseModel):
    """In-progress board state pushed by the client after each move.

    fen: full FEN of the current position.  Validated through the
        canonical ``_validate_fen_field`` shared with /move, /live/move,
        /analyze, /explain, /chat — 100-char cap, six FEN fields,
        verified parseable by ``chess.Board()``.  Pre-Sprint-5.B
        validation only rejected control chars + capped at 256, so a
        256-char malformed FEN was accepted, stored, and later served
        back to clients via /game/active (audit finding F-10).  The
        unified validator closes that path while staying compatible
        with every legitimate FEN length the Android client emits.
    uci_history: comma-separated UCI moves (e.g. "e2e4,e7e5,g1f3").
        Bounded at 16 KB — enough for a 2000-move game which is
        well beyond any realistic length.
    """

    fen: str
    uci_history: str = ""

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, v: str) -> str:
        return _validate_fen_field(v)

    @field_validator("uci_history")
    @classmethod
    def validate_uci_history(cls, v: str) -> str:
        if len(v) > 16_384:
            raise ValueError("uci_history too long (max 16384 chars)")
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError("uci_history contains control characters")
        return v
