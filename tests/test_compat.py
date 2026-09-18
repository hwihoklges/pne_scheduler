"""Exercise the Python 3.10 fallback even on newer CI interpreters."""

import builtins
from enum import auto
import json
from pathlib import Path
import runpy

import pytest

from pne_scheduler import _compat


@pytest.mark.parametrize("fallback", [False, True])
def test_string_enum_contract(monkeypatch, fallback):
    original_import = builtins.__import__

    def without_strenum(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "enum" and "StrEnum" in fromlist:
            raise ImportError("StrEnum is unavailable on Python 3.10")
        return original_import(name, globals, locals, fromlist, level)

    if fallback:
        monkeypatch.setattr(builtins, "__import__", without_strenum)
    enum_type = runpy.run_path(str(Path(_compat.__file__)))["StrEnum"]

    class Kind(enum_type):
        EXPLICIT = "explicit_value"
        AUTOMATIC = auto()

    assert Kind.EXPLICIT == "explicit_value"
    assert str(Kind.EXPLICIT) == "explicit_value"
    assert f"{Kind.EXPLICIT}" == "explicit_value"
    assert format(Kind.EXPLICIT, ">16") == "  explicit_value"
    assert json.dumps({"kind": Kind.EXPLICIT}) == '{"kind": "explicit_value"}'
    assert Kind("explicit_value") is Kind.EXPLICIT
    assert Kind.AUTOMATIC.value == "automatic"