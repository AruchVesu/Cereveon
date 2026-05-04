"""
AST-based isolation tests: StockfishAdapter must never be imported from live modules.

llm/seca/engines/stockfish/adapter.py contains StockfishAdapter, a simple
depth-limited wrapper. It is not wired into any live request path and must
not be imported from server.py, host_app.py, elite_engine_service.py,
engine_eval.py, or pool.py.

These tests parse each live module with the ast module (no execution) and
verify that neither the name "StockfishAdapter" nor the adapter module path
appears in any import statement.

Note: dynamic imports via importlib.import_module or __import__ are not
detected by this approach. That path is considered out of scope because
no live module in this codebase uses dynamic imports for engine adapters.
"""

import ast
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_LIVE_MODULES = [
    "llm/server.py",
    "llm/host_app.py",
    "llm/elite_engine_service.py",
    "llm/engine_eval.py",
    "llm/seca/engines/stockfish/pool.py",
]


def _parse_module(rel_path: str):
    path = _REPO_ROOT / rel_path
    source = path.read_text(encoding="utf-8")
    return ast.parse(source), rel_path


def _imported_names(tree: ast.AST) -> set:
    """Return all names imported via 'from X import Y' or 'import X as Y'."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def _imported_module_paths(tree: ast.AST) -> list:
    """Return all module paths referenced in import statements."""
    paths = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            paths.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                paths.append(alias.name)
    return paths


def test_stockfish_adapter_not_imported_as_name_in_live_modules():
    for rel_path in _LIVE_MODULES:
        tree, label = _parse_module(rel_path)
        imported = _imported_names(tree)
        assert "StockfishAdapter" not in imported, (
            f"{label} must not import StockfishAdapter. "
            "It is isolated dead code — see engine.md for rationale."
        )


def test_stockfish_adapter_module_not_imported_in_live_modules():
    for rel_path in _LIVE_MODULES:
        tree, label = _parse_module(rel_path)
        module_paths = _imported_module_paths(tree)
        assert not any("adapter" in p and "stockfish" in p for p in module_paths), (
            f"{label} must not import from the stockfish adapter module. "
            "Isolation contract: llm/tests/test_stockfish_adapter_isolation.py."
        )
