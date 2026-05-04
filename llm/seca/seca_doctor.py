#!/usr/bin/env python3
"""
SECA Doctor — Full backend health check

Runs end-to-end diagnostics:
- API availability
- Auth flow
- Game ingestion
- DB table integrity
- Dataset build
- World model training

Usage:
    python seca_doctor.py
"""

import subprocess
import sqlite3
import requests
import sys
import time
from pathlib import Path

BASE_URL = "http://127.0.0.1:5000"
DB_PATH = Path("data/seca.db")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def ok(msg):
    print(f"[PASS] {msg}")


def fail(msg):
    print(f"[FAIL] {msg}")


def info(msg):
    print(f"[INFO] {msg}")


# ---------------------------------------------------------------------
# 1. API Ping
# ---------------------------------------------------------------------


def check_api():
    try:
        r = requests.get(f"{BASE_URL}/docs")
        if r.status_code == 200:
            ok("API reachable")
            return True
        fail(f"API returned status {r.status_code}")
        return False
    except Exception as e:
        fail(f"API unreachable: {e}")
        return False


# ---------------------------------------------------------------------
# 2. Auth Flow
# ---------------------------------------------------------------------


def check_auth():
    email = f"doctor_{int(time.time())}@seca.ai"
    password = "test123"

    try:
        # register
        r = requests.post(f"{BASE_URL}/auth/register", json={"email": email, "password": password})
        if r.status_code != 200:
            fail("Register failed")
            return None

        # login
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": email, "password": password, "device_info": "doctor"},
        )

        if r.status_code != 200:
            fail("Login failed")
            return None

        token = r.json()["access_token"]

        # /me
        r = requests.get(f"{BASE_URL}/auth/me", headers={"Authorization": f"Bearer {token}"})

        if r.status_code == 200:
            ok("Auth flow working")
            return token

        fail("/auth/me failed")
        return None

    except Exception as e:
        fail(f"Auth exception: {e}")
        return None


# ---------------------------------------------------------------------
# 3. Game ingestion
# ---------------------------------------------------------------------


def check_game_finish(token):
    payload = {
        "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6",
        "result": "win",
        "accuracy": 0.8,
        "weaknesses": {"time_management": 1.0},
    }

    try:
        r = requests.post(
            f"{BASE_URL}/game/finish",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

        if r.status_code == 200:
            ok("Game ingestion works")
            return True

        fail(f"/game/finish failed: {r.status_code} {r.text}")
        return False

    except Exception as e:
        fail(f"Game finish exception: {e}")
        return False


# ---------------------------------------------------------------------
# 4. DB integrity
# ---------------------------------------------------------------------


def check_db():
    if not DB_PATH.exists():
        fail("DB file missing")
        return False

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    _ALLOWED_TABLES = frozenset(
        {
            "players",
            "game_events",
            "rating_updates",
            "confidence_updates",
            "analytics_events",
        }
    )
    tables = list(_ALLOWED_TABLES)

    ok_all = True

    for t in tables:
        if t not in _ALLOWED_TABLES:
            fail(f"Unexpected table name rejected: {t}")
            ok_all = False
            continue
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")  # nosec: validated against allowlist above
            count = cur.fetchone()[0]
            info(f"{t}: {count}")
            if count == 0:
                ok_all = False
        except Exception as e:
            fail(f"{t} error: {e}")
            ok_all = False

    conn.close()

    if ok_all:
        ok("DB tables populated")
    else:
        fail("Some DB tables empty or missing")

    return ok_all


# ---------------------------------------------------------------------
# 5. Dataset build
# ---------------------------------------------------------------------


def run_module(module):
    try:
        subprocess.check_call([sys.executable, "-m", module])
        return True
    except subprocess.CalledProcessError:
        return False


def check_dataset():
    if run_module("llm.seca.brain.data.build_world_model_dataset"):
        ok("Dataset build succeeded")
        return True

    fail("Dataset build failed")
    return False


# ---------------------------------------------------------------------
# 6. World model training
# ---------------------------------------------------------------------


def check_training():
    if run_module("llm.seca.brain.world_model.train_regression"):
        ok("World model training succeeded")
        return True

    fail("World model training failed")
    return False


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------


def main():
    print("=== SECA DOCTOR ===")

    if not check_api():
        return

    token = check_auth()
    if not token:
        return

    check_game_finish(token)
    check_db()
    check_dataset()
    check_training()

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
