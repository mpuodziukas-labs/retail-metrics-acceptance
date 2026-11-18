"""Tests read and write text with an explicit UTF-8 encoding, so no test depends on the locale."""
import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def text_calls_without_utf8(source):
    """Return (text-mode calls examined, their line numbers lacking encoding="utf-8").

    Examined: open(), Path.open(), read_text() and write_text(). Binary opens are skipped.
    """
    examined, missing = 0, []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name == "open":
            mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
            position = 1 if isinstance(fn, ast.Name) else 0
            if mode is None and len(node.args) > position:
                mode = node.args[position]
            if isinstance(mode, ast.Constant) and "b" in str(mode.value):
                continue
        elif name not in ("read_text", "write_text"):
            continue
        examined += 1
        enc = next((k.value for k in node.keywords if k.arg == "encoding"), None)
        if not (isinstance(enc, ast.Constant) and enc.value == "utf-8"):
            missing.append(node.lineno)
    return examined, missing


def test_every_text_open_read_and_write_in_the_tests_names_utf_8():
    files = sorted(TESTS.glob("*.py"))
    assert len(files) >= 8
    total, bad = 0, []
    for path in files:
        examined, missing = text_calls_without_utf8(path.read_text(encoding="utf-8"))
        total += examined
        bad += [f"{path.name}:{n}" for n in missing]
    assert total >= 40
    assert not bad, bad


@pytest.mark.parametrize("snippet", [
    "open(p)", "open(p, 'w', newline='')", "open(p, encoding='latin-1')",
    "p.read_text()", "p.write_text('x')", "p.open()", "p.open('w')", "p.read_text(errors='ignore')",
    "open(p, encoding=enc)"])
def test_the_hygiene_check_flags_a_call_without_utf_8(snippet):
    assert text_calls_without_utf8(snippet) == (1, [1])


@pytest.mark.parametrize("snippet", [
    "open(p, encoding='utf-8')", "open(p, 'w', newline='', encoding='utf-8')",
    "p.read_text(encoding='utf-8')", "p.write_text('x', encoding='utf-8')",
    "p.open('a', encoding='utf-8')"])
def test_the_hygiene_check_accepts_a_call_with_utf_8(snippet):
    assert text_calls_without_utf8(snippet) == (1, [])


@pytest.mark.parametrize("snippet", [
    "open(p, 'rb')", "open(p, 'wb')", "p.open('rb')", "open(p, mode='ab')", "p.read_bytes()"])
def test_the_hygiene_check_skips_binary_access(snippet):
    assert text_calls_without_utf8(snippet) == (0, [])
