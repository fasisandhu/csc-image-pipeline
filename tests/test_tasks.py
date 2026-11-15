import pytest

from studio.tasks import read_brief


class FakeBrain:
    def __init__(self, data):
        self.data, self.calls = data, []

    def ask_json(self, task, prompt, schema, images=(), user="owner"):
        self.calls.append((task, prompt, schema, user))
        return type("R", (), {"data": self.data, "provider": "fake", "fallback": False})()


def run(data, answers=None):
    return read_brief("anything", FakeBrain(data), answers)


def test_valid_answers_kept():
    r = run({"colour": "olive", "decoration": "design", "placement": "full back"})
    assert r["answers"] == {"colour": "olive", "decoration": "design", "placement": "full back"}
    assert r["provider"] == "fake" and r["fallback"] is False


def test_invented_values_dropped():
    assert run({"colour": "#123456", "gsm": "500", "cast": "Zara"})["answers"] == {}


def test_unknown_colour_becomes_hint():
    r = run({"colour": None, "colour_word": "teal"})
    assert "colour" not in r["answers"] and r["colour_hint"] == "teal" and "colour" in r["unknown"]


@pytest.mark.parametrize("word, hint", [
    ("teal", "teal"),
    ("  dark green ", "dark green"),
    ("blue-grey", "blue-grey"),
    ("Payne's grey", "Payne's grey"),
    ("Payne’s grey", "Payne’s grey"),
    ("a" * 40, "a" * 40),
    ("a" * 41, None),
    ("teal/../../.env", None),
    ("GEMINI_API_KEY=abc", None),
    ("teal 2", None),
    ("", None),
])
def test_colour_hint_is_plain_words_only(word, hint):
    """Defence in depth: the free-text hint reaches the browser, so only a short plain colour word passes."""
    assert run({"colour": None, "colour_word": word})["colour_hint"] == hint


def test_existing_answers_win():
    assert run({"gsm": "380"}, {"gsm": 450})["answers"] == {"gsm": 450}


def test_dependent_values_kept_together():
    r = run({"decoration": "design", "placement": "full back", "recipe": "on-model-back"})
    assert r["answers"] == {"decoration": "design", "placement": "full back", "recipe": "on-model-back"}


def test_placement_dropped_when_recipe_cannot_show_it():
    r = run({"recipe": "packshot-front", "placement": "full back"})
    assert r["answers"] == {"recipe": "packshot-front"}


def test_non_scalar_values_dropped_without_error():
    r = run({"colour": ["olive"], "gsm": {"a": 1}, "cast": "Amir"})
    assert "colour" not in r["answers"] and "gsm" not in r["answers"]


def test_numbers_become_int():
    r = run({"gsm": "380", "decoration": "design", "placement": "full back", "size_cm": "30"})
    assert r["answers"]["gsm"] == 380 and r["answers"]["size_cm"] == 30
    assert isinstance(r["answers"]["gsm"], int) and isinstance(r["answers"]["size_cm"], int)


def test_schema_and_prompt_cover_hidden_sections():
    b = FakeBrain({})
    read_brief("olive hoodie", b)
    task, prompt, schema, user = b.calls[0]
    assert task == "read_brief" and user == "owner"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert {"placement", "size_cm", "colour", "colour_word"} <= set(schema["properties"])
    assert "full back" in prompt and "olive hoodie" in prompt


def test_validation_repeats_until_nothing_changes(monkeypatch):
    """A value accepted only because of one that is then dropped goes too (as app.usable does)."""
    from studio import tasks

    real = tasks.valid_value

    def valid(sid, value, answers):
        # stand-in for a view-aware size limit: with no placement, a front packshot shows at most 22 cm
        if sid == "size_cm" and answers.get("recipe") == "packshot-front" and "placement" not in answers:
            return value <= 22
        return real(sid, value, answers)

    monkeypatch.setattr(tasks, "valid_value", valid)
    r = run({"recipe": "packshot-front", "decoration": "design", "placement": "full back", "size_cm": 30})
    # full back is not visible from the front, so the 30 cm it allowed is dropped with it
    assert r["answers"] == {"recipe": "packshot-front", "decoration": "design"}
    r = run({"recipe": "packshot-front", "decoration": "design", "placement": "full back", "size_cm": 20})
    assert r["answers"] == {"recipe": "packshot-front", "decoration": "design", "size_cm": 20}
