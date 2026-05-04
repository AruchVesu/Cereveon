"""
Adversarial functional probe for all 9 security fixes.
Runs standalone — no live server, no Stockfish, no network.
Exit code 0 = all checks passed, non-zero = at least one failure.
"""

import ast
import inspect
import sys
import textwrap
import traceback

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results = []


def check(label: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(ok)


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# SVD-03 — Password length caps
# ---------------------------------------------------------------------------
section("SVD-03: Password length caps")

# 3a: Pydantic validators in router.py
try:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from pydantic import ValidationError
    from llm.seca.auth.router import RegisterRequest, LoginRequest, ChangePasswordRequest

    long_pw = "x" * 1001

    # RegisterRequest rejects long password
    try:
        RegisterRequest(email="a@b.com", password=long_pw)
        check("RegisterRequest rejects password > 1000 chars", False, "no error raised")
    except ValidationError:
        check("RegisterRequest rejects password > 1000 chars", True)

    # RegisterRequest accepts valid password
    try:
        r = RegisterRequest(email="a@b.com", password="validpass")
        check("RegisterRequest accepts normal password", True)
    except ValidationError as e:
        check("RegisterRequest accepts normal password", False, str(e))

    # LoginRequest rejects long password
    try:
        LoginRequest(email="a@b.com", password=long_pw)
        check("LoginRequest rejects password > 1000 chars", False, "no error raised")
    except ValidationError:
        check("LoginRequest rejects password > 1000 chars", True)

    # ChangePasswordRequest rejects long current_password
    try:
        ChangePasswordRequest(current_password=long_pw, new_password="shortok1")
        check("ChangePasswordRequest rejects current_password > 1000 chars", False, "no error raised")
    except ValidationError:
        check("ChangePasswordRequest rejects current_password > 1000 chars", True)

    # ChangePasswordRequest rejects long new_password
    try:
        ChangePasswordRequest(current_password="shortok1", new_password=long_pw)
        check("ChangePasswordRequest rejects new_password > 1000 chars", False, "no error raised")
    except ValidationError:
        check("ChangePasswordRequest rejects new_password > 1000 chars", True)

except Exception as e:
    traceback.print_exc()
    check("SVD-03 router Pydantic import/setup", False, str(e))

# 3b: Service-layer caps
try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from llm.seca.auth.models import Base
    from llm.seca.auth.service import AuthService

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    svc = AuthService(db)

    # register rejects long password
    try:
        svc.register("test@x.com", "x" * 1001)
        check("Service.register rejects password > 1000 chars", False, "no error raised")
    except ValueError as e:
        check("Service.register rejects password > 1000 chars", "1000" in str(e))

    # register accepts valid password
    try:
        svc.register("ok@x.com", "validpass1")
        check("Service.register accepts normal password", True)
    except ValueError as e:
        check("Service.register accepts normal password", False, str(e))

    # change_password rejects long current_password before verify (no verify overhead)
    from llm.seca.auth.models import Player
    from llm.seca.auth.hashing import hash_password
    fake_player = Player(email="dummy@x.com", password_hash=hash_password("correct1"))

    try:
        svc.change_password(fake_player, "x" * 1001, "newpass12")
        check("Service.change_password rejects current_password > 1000 chars", False, "no error raised")
    except ValueError as e:
        check("Service.change_password rejects current_password > 1000 chars", "1000" in str(e))

    try:
        svc.change_password(fake_player, "correct1", "x" * 1001)
        check("Service.change_password rejects new_password > 1000 chars", False, "no error raised")
    except ValueError as e:
        check("Service.change_password rejects new_password > 1000 chars", "1000" in str(e))

    db.close()

except Exception as e:
    traceback.print_exc()
    check("SVD-03 service layer", False, str(e))


# ---------------------------------------------------------------------------
# SVD-06 — UCI move validation
# ---------------------------------------------------------------------------
section("SVD-06: UCI move validation")

try:
    from llm.server import LiveMoveRequest

    invalid_ucis = ["0000", "AAAA", "####", "e2e2e2", "", "i9a1", "a1a9", "e2e4p"]
    for uci in invalid_ucis:
        try:
            LiveMoveRequest(fen="startpos", uci=uci)
            check(f"LiveMoveRequest rejects uci={uci!r}", False, "no error raised")
        except ValidationError:
            check(f"LiveMoveRequest rejects uci={uci!r}", True)

    valid_ucis = ["e2e4", "g1f3", "e7e8q", "a7a8r", "h7h8n", "b2b1b"]
    for uci in valid_ucis:
        try:
            LiveMoveRequest(fen="startpos", uci=uci)
            check(f"LiveMoveRequest accepts uci={uci!r}", True)
        except ValidationError as e:
            check(f"LiveMoveRequest accepts uci={uci!r}", False, str(e))

except Exception as e:
    traceback.print_exc()
    check("SVD-06 UCI validation import/setup", False, str(e))


# ---------------------------------------------------------------------------
# SVD-07 — FEN validation in server.py
# ---------------------------------------------------------------------------
section("SVD-07: FEN validation (server.py AnalyzeRequest / LiveMoveRequest)")

try:
    from llm.server import AnalyzeRequest

    invalid_fens = [
        "not a fen at all",
        "a b c d e f",  # 6 parts but semantically invalid
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1 extra",  # 7 parts
        "x" * 101,
    ]
    for fen in invalid_fens:
        try:
            AnalyzeRequest(fen=fen, stockfish_json=None)
            check(f"AnalyzeRequest rejects bad FEN ({fen[:30]!r}...)", False, "no error raised")
        except (ValidationError, Exception):
            check(f"AnalyzeRequest rejects bad FEN ({fen[:30]!r}...)", True)

    valid_fens = [
        "startpos",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    ]
    for fen in valid_fens:
        try:
            AnalyzeRequest(fen=fen, stockfish_json=None)
            check(f"AnalyzeRequest accepts valid FEN ({fen[:30]!r})", True)
        except ValidationError as e:
            check(f"AnalyzeRequest accepts valid FEN ({fen[:30]!r})", False, str(e))

except Exception as e:
    traceback.print_exc()
    check("SVD-07 FEN validation", False, str(e))


# ---------------------------------------------------------------------------
# SVD-08 — player_id length cap
# ---------------------------------------------------------------------------
section("SVD-08: player_id length cap (StartGameRequest)")

try:
    from llm.server import StartGameRequest

    try:
        StartGameRequest(player_id="x" * 101)
        check("StartGameRequest rejects player_id > 100 chars", False, "no error raised")
    except ValidationError:
        check("StartGameRequest rejects player_id > 100 chars", True)

    try:
        StartGameRequest(player_id="abc-123")
        check("StartGameRequest accepts normal player_id", True)
    except ValidationError as e:
        check("StartGameRequest accepts normal player_id", False, str(e))

except Exception as e:
    traceback.print_exc()
    check("SVD-08 player_id", False, str(e))


# ---------------------------------------------------------------------------
# NEW-01 — change_password rate limit
# ---------------------------------------------------------------------------
section("NEW-01: change_password carries @limiter.limit")

try:
    import llm.seca.auth.router as auth_router_mod
    src = inspect.getsource(auth_router_mod)
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "change_password":
            for dec in node.decorator_list:
                dec_src = ast.unparse(dec)
                if "limiter.limit" in dec_src:
                    found = True
    check("change_password has @limiter.limit decorator", found)

except Exception as e:
    traceback.print_exc()
    check("NEW-01 rate limit check", False, str(e))


# ---------------------------------------------------------------------------
# NEW-02 — start_game rate limit
# ---------------------------------------------------------------------------
section("NEW-02: start_game carries @limiter.limit")

try:
    import llm.server as server_mod
    src = inspect.getsource(server_mod)
    tree = ast.parse(src)

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "start_game":
            for dec in node.decorator_list:
                dec_src = ast.unparse(dec)
                if "limiter.limit" in dec_src:
                    found = True
    check("start_game has @limiter.limit decorator", found)

except Exception as e:
    traceback.print_exc()
    check("NEW-02 rate limit check", False, str(e))


# ---------------------------------------------------------------------------
# NEW-03 — explain rate limit (server.py)
# ---------------------------------------------------------------------------
section("NEW-03: explain carries @limiter.limit")

try:
    found = False
    for node in ast.walk(tree):  # reuse tree from NEW-02
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "explain":
            for dec in node.decorator_list:
                dec_src = ast.unparse(dec)
                if "limiter.limit" in dec_src:
                    found = True
    check("explain has @limiter.limit decorator", found)

except Exception as e:
    traceback.print_exc()
    check("NEW-03 rate limit check", False, str(e))


# ---------------------------------------------------------------------------
# NEW-04 — seca/inference/router.py ExplainRequest FEN validation
# ---------------------------------------------------------------------------
section("NEW-04: ExplainRequest FEN validation (seca/inference/router.py)")

try:
    from llm.seca.inference.router import ExplainRequest

    invalid_fens = [
        "garbage",
        "a b c d e f",
        "x" * 101,
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1 extra",
    ]
    for fen in invalid_fens:
        try:
            ExplainRequest(fen=fen)
            check(f"ExplainRequest rejects bad FEN ({fen[:30]!r})", False, "no error raised")
        except ValidationError:
            check(f"ExplainRequest rejects bad FEN ({fen[:30]!r})", True)

    valid_fens = [
        "startpos",
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    ]
    for fen in valid_fens:
        try:
            ExplainRequest(fen=fen)
            check(f"ExplainRequest accepts valid FEN ({fen[:30]!r})", True)
        except ValidationError as e:
            check(f"ExplainRequest accepts valid FEN ({fen[:30]!r})", False, str(e))

except Exception as e:
    traceback.print_exc()
    check("NEW-04 ExplainRequest FEN validation", False, str(e))


# ---------------------------------------------------------------------------
# NEW-05 — stockfish_json size limit in AnalyzeRequest
# ---------------------------------------------------------------------------
section("NEW-05: stockfish_json size limit (AnalyzeRequest)")

try:
    # 100-key dict rejected
    big_dict = {str(i): i for i in range(100)}
    try:
        AnalyzeRequest(fen="startpos", stockfish_json=big_dict)
        check("AnalyzeRequest rejects stockfish_json with 100 keys", False, "no error raised")
    except ValidationError:
        check("AnalyzeRequest rejects stockfish_json with 100 keys", True)

    # 51-key dict also rejected
    big_dict2 = {str(i): i for i in range(51)}
    try:
        AnalyzeRequest(fen="startpos", stockfish_json=big_dict2)
        check("AnalyzeRequest rejects stockfish_json with 51 keys", False, "no error raised")
    except ValidationError:
        check("AnalyzeRequest rejects stockfish_json with 51 keys", True)

    # Normal dict accepted
    normal_dict = {"score": 42, "depth": 20, "bestmove": "e2e4"}
    try:
        AnalyzeRequest(fen="startpos", stockfish_json=normal_dict)
        check("AnalyzeRequest accepts normal stockfish_json", True)
    except ValidationError as e:
        check("AnalyzeRequest accepts normal stockfish_json", False, str(e))

    # None accepted
    try:
        AnalyzeRequest(fen="startpos", stockfish_json=None)
        check("AnalyzeRequest accepts stockfish_json=None", True)
    except ValidationError as e:
        check("AnalyzeRequest accepts stockfish_json=None", False, str(e))

except Exception as e:
    traceback.print_exc()
    check("NEW-05 stockfish_json size limit", False, str(e))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section("SUMMARY")
total = len(results)
passed = sum(results)
failed = total - passed
print(f"\n  {passed}/{total} checks passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    sys.exit(1)
else:
    print("  — all good!")
    sys.exit(0)
