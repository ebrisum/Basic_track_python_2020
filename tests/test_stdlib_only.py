"""The engine stays pure standard library. The model stack does not.

`RQ-22` takes the project's first dependency — PyTorch, for `TCG_AI_BUILD.md`
sections 16-22 — and the whole case for taking it rests on it being *scoped*.
A dependency that quietly reaches into the rules layer would undo that, and it
would happen the way these things always happen: one convenient import, in one
file, that nobody notices until the engine no longer runs on a clean Python.

So this is asserted rather than intended. Someone who wants to play Riftbound
or run the validation suite must still need nothing installed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Packages that must import nothing outside the standard library.
PURE = ("engine", "cards", "agents", "analysis", "learning", "decks", "frontend")
PURE_FILES = ("play.py", "cli.py")

# Everything the standard library ships, as this interpreter sees it, plus the
# project's own top-level packages.
STDLIB = set(sys.stdlib_module_names)
OWN = {"engine", "cards", "agents", "analysis", "learning", "decks", "frontend",
       "data", "tests", "play", "cli", "model"}


def _sources():
    for package in PURE:
        directory = ROOT / package
        if directory.is_dir():
            for path in sorted(directory.rglob("*.py")):
                if "__pycache__" not in path.parts:
                    yield path
    for name in PURE_FILES:
        path = ROOT / name
        if path.exists():
            yield path


def _imports(path: Path, *, import_time_only: bool = True) -> set[str]:
    """Module names this file imports.

    `import_time_only` skips function bodies, because an import inside a
    function is precisely how an optional tool stays optional -- it costs
    nothing to anyone who does not call it. `cards/extract.py` does this with
    `anthropic`: the LLM-assisted card scripter needs it, and a person playing
    Riftbound never runs that code path, so the module still imports on a
    clean Python. Class bodies *are* walked, since they execute on import.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()

    def walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if import_time_only and isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if isinstance(child, ast.Import):
                names.update(alias.name.split(".")[0] for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                if not child.level and child.module:   # relative stays inside
                    names.add(child.module.split(".")[0])
            walk(child)

    walk(tree)
    return names


@pytest.mark.parametrize("path", list(_sources()), ids=lambda p: str(p.relative_to(ROOT)))
def test_the_engine_layers_import_only_the_standard_library(path):
    outside = sorted(_imports(path) - STDLIB - OWN)
    assert not outside, (
        f"{path.relative_to(ROOT)} imports {outside}, which is outside the "
        f"standard library. The engine's purity is the reason RQ-22's "
        f"dependency could be taken at all -- it belongs in model/, not here."
    )


def test_the_model_package_is_where_torch_lives():
    """The converse: the dependency exists and is not merely declared."""
    if not (ROOT / "model").is_dir():
        pytest.skip("model/ does not exist yet")
    torch_users = [
        path for path in sorted((ROOT / "model").rglob("*.py"))
        if "__pycache__" not in path.parts and "torch" in _imports(path)
    ]
    assert torch_users, "model/ exists but nothing in it imports torch"


def test_an_optional_dependency_may_only_be_imported_lazily():
    """The exemption above is narrow, and this is what keeps it narrow.

    `cards/extract.py` is allowed to use `anthropic` because the import sits
    inside a function behind a try/except. If someone hoists it to the top of
    the file, `cards.extract` stops importing on a clean Python and the
    exemption silently becomes a dependency.
    """
    path = ROOT / "cards" / "extract.py"
    if not path.exists():
        pytest.skip("cards/extract.py has gone away")
    assert "anthropic" not in _imports(path), "hoisted to import time"
    assert "anthropic" in _imports(path, import_time_only=False), (
        "the lazy import has gone -- update or delete this test"
    )
