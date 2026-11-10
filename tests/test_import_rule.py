"""Lint rule: no actuation without policy.

In mcp_server/, SimBridge actuating methods may be called only inside
Runtime._execute, and _execute may be called only from Runtime.actuate, which
calls policy.check() first. Fails the build if anyone adds a shortcut.
"""

import ast
import sys
from pathlib import Path

ACTUATING = {"arm", "takeoff", "goto", "return_to_home", "land", "reset", "hard_reset"}
ROOT = Path(__file__).resolve().parents[1] / "mcp_server"
ALLOWED_RESET = {("Runtime", "start_episode")}  # runner-only lifecycle, not an agent tool


def _calls(tree):
    """Yield (enclosing class, enclosing function, call node)."""

    def walk(node, cls=None, fn=None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from walk(child, child.name, fn)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from walk(child, cls, child.name if fn is None or cls else fn)
            else:
                if isinstance(child, ast.Call):
                    yield cls, fn, child
                yield from walk(child, cls, fn)

    yield from walk(tree)


def _attr_chain(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _violations():
    out = []
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for cls, fn, call in _calls(tree):
            if not isinstance(call.func, ast.Attribute):
                continue
            name, chain = call.func.attr, _attr_chain(call.func)
            receiver = chain.rsplit(".", 1)[0]
            if name in ACTUATING and "bridge" in receiver or receiver == "b" and name in ACTUATING:
                if (cls, fn) == ("Runtime", "_execute"):
                    continue
                if name in ("reset", "hard_reset") and (cls, fn) in ALLOWED_RESET:
                    continue
                out.append(f"{path.name}:{call.lineno} {chain}() in {cls}.{fn}")
            if name == "_execute" and (cls, fn) != ("Runtime", "actuate"):
                out.append(f"{path.name}:{call.lineno} _execute() called from {cls}.{fn}")
    return out


def test_no_actuation_outside_execute():
    assert _violations() == []


def test_actuate_checks_policy_before_execute():
    src = (ROOT / "runtime.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "actuate")
    body = ast.get_source_segment(src, fn)
    assert body.index("policy.check(") < body.index("self._execute(")


def test_rule_catches_a_shortcut(tmp_path, monkeypatch):
    """The lint must actually fire: plant a direct bridge call and expect a violation."""
    bad = tmp_path / "bad.py"
    bad.write_text("async def sneaky(rt):\n    await rt.bridge.goto(1, 2, 3)\n")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert any("sneaky" in v for v in _violations())
