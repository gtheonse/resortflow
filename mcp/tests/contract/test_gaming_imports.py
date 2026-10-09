"""T055: Static-import test for the gaming domain's read-only guarantee.

AST-inspects `mcp/gateway/gaming/server.py` and asserts it does not import
`_shared.idempotency` at all — mirroring the read-only casino sim's own pattern
(a module with no mutating tools has no reason to ever construct an idempotency key).

Gaming *does* still need `_shared.entitlements` enforced (`gaming:read` is a real
scope check), which flows through `_shared.registry.register_tool` rather than being
imported directly by `gaming/server.py` — so this test asserts the *registry* import
(the actual enforcement path this module goes through) is present, and does not
flag it.
"""

from __future__ import annotations

import ast
import pathlib

GAMING_SERVER_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "gateway" / "gaming" / "server.py"
)


def _imported_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                names.add(f"{module}.{alias.name}" if module else alias.name)
    return names


def _parse_gaming_server() -> ast.Module:
    source = GAMING_SERVER_PATH.read_text()
    return ast.parse(source, filename=str(GAMING_SERVER_PATH))


def test_gaming_server_file_exists():
    assert GAMING_SERVER_PATH.is_file(), f"expected {GAMING_SERVER_PATH} to exist"


def test_gaming_server_does_not_import_idempotency():
    tree = _parse_gaming_server()
    imported = _imported_module_names(tree)
    offending = {name for name in imported if "idempotency" in name}
    assert not offending, (
        f"gateway/gaming/server.py must not import _shared.idempotency at all "
        f"(gaming is read-only, T055/SC-011); found: {sorted(offending)}"
    )


def test_gaming_server_does_import_entitlements_enforcement_path():
    """Gaming still enforces `gaming:read` — via `_shared.registry` (which itself
    imports `_shared.entitlements` and calls `check_entitlement` on every tool call),
    so the module's actual enforcement import is `registry`, not `entitlements`
    directly. Assert the enforcement path is present; do not flag it as an
    unnecessary import the way `idempotency` is flagged above."""
    tree = _parse_gaming_server()
    imported = _imported_module_names(tree)
    assert any("registry" in name for name in imported), (
        "gateway/gaming/server.py must import the _shared.registry enforcement path "
        "(register_tool), which enforces gaming:read via _shared.entitlements"
    )


def test_gaming_server_source_never_calls_idempotency_key_derivation():
    """Belt-and-braces check alongside the AST import check: the key-derivation
    helper itself is never called anywhere in the file (the module docstring is
    allowed to *mention* `_shared.idempotency` by name to explain the omission —
    only an actual import or call would break the read-only guarantee)."""
    tree = _parse_gaming_server()
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "sub_call_key" not in called_names
    assert "validate_idempotency_key" not in called_names
