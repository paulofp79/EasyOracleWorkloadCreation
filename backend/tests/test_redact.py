from app.redact import MASK, Redactor, mask_argv


def test_redacts_all_occurrences_longest_first():
    r = Redactor(["abc", "abcdef"])
    assert r.text("x abcdef y abc") == f"x {MASK} y {MASK}"


def test_redacts_nested_objects():
    r = Redactor(["s3cret"])
    out = r.obj({"a": ["s3cret", {"b": "pw=s3cret"}], "n": 1})
    assert out == {"a": [MASK, {"b": f"pw={MASK}"}], "n": 1}


def test_empty_redactor_is_noop():
    r = Redactor([None, ""])
    assert not r
    assert r.text("anything") == "anything"


def test_mask_argv():
    argv = ["oewizard", "-u", "soe", "-p", "pw1", "-dba", "sys as sysdba", "-dbap", "pw2", "-v"]
    assert mask_argv(argv) == ["oewizard", "-u", "soe", "-p", MASK, "-dba", "sys as sysdba", "-dbap", MASK, "-v"]
