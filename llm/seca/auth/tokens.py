import os
import secrets
from datetime import datetime, timedelta

import jwt

_IS_PROD = os.getenv("SECA_ENV", "dev") in {"prod", "production"}
_raw_secret_key = os.getenv("SECRET_KEY", "")

if _IS_PROD and not _raw_secret_key:
    raise RuntimeError(
        "SECRET_KEY env var is required in production (SECA_ENV=prod). "
        "Set a stable value of at least 32 characters."
    )

SECRET_KEY = _raw_secret_key or secrets.token_hex(32)
if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY must be at least 32 characters.")
ALGORITHM = "HS256"

# JWT exp claim — 24 hours.  The router's get_current_player now
# attaches a fresh X-Auth-Token header on every authenticated
# response, so an active user's client transparently rotates the
# stored JWT every API call and never sees an expiry-driven
# bounce.  Tight 24h cap means a stolen JWT expires the same day
# regardless of session-sliding state on the server.
#
# Lineage:
#   pre-2026-04: 15 min — bounced active users every 15 min (no refresh)
#   2026-04:     7 days — paired with sliding session, no refresh
#   2026-04+ (now): 24 h — paired with X-Auth-Token refresh on every
#                          authenticated response, so daily-active
#                          users stay logged in indefinitely while
#                          the security window stays tight.
ACCESS_EXPIRE_MINUTES = 60 * 24  # 24 hours


def create_access_token(player_id: str, session_id: str) -> str:
    payload = {
        "player_id": player_id,
        "session_id": session_id,
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
