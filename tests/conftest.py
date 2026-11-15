"""Shared test isolation."""
import pytest

from pipeline import compile as compiler


@pytest.fixture(autouse=True)
def isolated_prompt_rules(monkeypatch, tmp_path):
    """Every test compiles with an empty rules file, never brand/prompt_rules.json: an owner switching a rule on must
    not change what the suite sees. A test that needs rules points compiler.RULES_PATH at its own file."""
    path = tmp_path / "isolated_prompt_rules.json"
    path.write_text('{"rules": []}\n', encoding="utf-8")
    monkeypatch.setattr(compiler, "RULES_PATH", path)
    return path
