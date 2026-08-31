"""Catch names that only exist on paths nobody exercises.

This exists because of a real crash. `agent/llm_ollama.py` built its own
malformed-JSON repair message and called `_errors(exc)` -- a function that does
not exist in that module. The branch is only reachable when the model returns
JSON that fails validation, which it never did until Ollama Cloud got slow
enough to start returning junk. Then a 300-payment run died two hours in with a
NameError, having burned the quota.

`tests/test_imports.py` could not catch it: importing a module does not execute
the inside of an `except ValidationError` block. Only static analysis does.

Kept narrow on purpose. This asserts there are no undefined names and no
syntax-level mistakes; it is not a style gate and does not fail on unused
imports, line length, or anything else a formatter would argue about.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PACKAGES = ("sim", "agent", "evaluation", "integrations", "scripts", "tests")

# Everything pyflakes reports that is a genuine defect rather than a preference.
# "imported but unused" is deliberately excluded: several modules re-export for
# convenience, and failing on it would make this test annoying enough to delete.
FATAL = (
    "undefined name",
    "syntax error",
    "f-string is missing placeholders",
    "redefinition of unused",
    "is assigned to but never used",
)


def _pyflakes() -> list[str] | None:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", *PACKAGES],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode and not proc.stdout and "No module named" in proc.stderr:
        return None
    return proc.stdout.splitlines()


def test_no_undefined_names() -> None:
    lines = _pyflakes()
    if lines is None:
        pytest.skip("pyflakes not installed; pip install pyflakes to enable this check")

    real = [ln for ln in lines if any(f in ln.lower() for f in FATAL)]
    assert not real, (
        "static analysis found defects that only fire on paths tests do not reach:\n  "
        + "\n  ".join(real)
    )
