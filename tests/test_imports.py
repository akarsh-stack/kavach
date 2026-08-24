"""Every module must import.

Trivial, and it exists because a dataclass field-ordering mistake in
`agent/audit.py` survived a full 32-test run: nothing under tests/ imported that
module, so the TypeError only surfaced when a script happened to touch it.

A test suite that never imports half the package is not testing the package.
"""

from __future__ import annotations

import importlib
import pathlib
import pkgutil

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGES = ("agent", "sim", "core", "economics", "evaluation")


def _modules() -> list[str]:
    out = []
    for pkg in PACKAGES:
        path = REPO / pkg
        if not path.exists():
            continue
        out.append(pkg)
        for m in pkgutil.iter_modules([str(path)]):
            out.append(f"{pkg}.{m.name}")
    return sorted(out)


@pytest.mark.parametrize("name", _modules())
def test_module_imports(name: str) -> None:
    importlib.import_module(name)
