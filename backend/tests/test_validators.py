import pytest

from app import validators as v
from app.naming import connect_string, pdb_connect_string, pdb_name, pdb_range, service_name

from .conftest import VECTORS


def _cases(kind):
    for rule, sets in VECTORS.items():
        if rule.startswith("_"):
            continue
        for value in sets[kind]:
            yield pytest.param(rule, value, id=f"{rule}-{value!r}")


@pytest.mark.parametrize("rule,value", list(_cases("valid")))
def test_shared_vectors_valid(rule, value):
    v.RULES[rule](value)


@pytest.mark.parametrize("rule,value", list(_cases("invalid")))
def test_shared_vectors_invalid(rule, value):
    with pytest.raises(v.RuleError):
        v.RULES[rule](value)


def test_every_rule_has_vectors():
    assert set(v.RULES) == {k for k in VECTORS if not k.startswith("_")}


def test_normalisation():
    assert v.prefix("pdbpp") == "PDBPP"
    assert v.pdb_number("42") == 42
    assert v.scale(0.1) == "0.1"
    assert v.scale("5") == "5"
    assert v.start_delay("0.5") == 0.5
    assert v.tablespace("soetbs") == "SOETBS"
    assert v.boolean("false") is False
    assert v.scan_port("1521") == 1521


def test_profile_defaults_are_valid():
    clean = v.validate_profile_params({})
    assert clean["prefix"] == "PDBPP"
    assert (clean["load_first"], clean["load_last"]) == (222, 250)
    assert clean["disable_txns"] == "SQ,WQ,WA"


def test_range_order_and_unknown_field_reported_together():
    with pytest.raises(v.ValidationError) as exc:
        v.validate_profile_params({"workload_first": 10, "workload_last": 9, "bogus": 1, "users": "x"})
    assert set(exc.value.errors) == {"workload_last", "bogus", "users"}


def test_partial_validation():
    assert v.validate({"first": "3"}, {"first": "pdb_number", "last": "pdb_number"}, partial=True) == {"first": 3}
    with pytest.raises(v.ValidationError) as exc:
        v.validate({}, {"first": "pdb_number"})
    assert exc.value.errors == {"first": "is required"}


def test_naming():
    assert pdb_name("pdbpp", 7) == "PDBPP_007"
    assert pdb_range("PDBPP", 249, 250) == ["PDBPP_249", "PDBPP_250"]
    assert len(pdb_range("PDBPP", 1, 250)) == 250
    assert service_name("PDBPP_007", ".us.oracle.com") == "pdbpp_007.us.oracle.com"
    assert connect_string("scan", 1521, "svc") == "//scan:1521/svc"
    assert pdb_connect_string("PDBPP_001", "scaqaq01-scan1.us.oracle.com", 1521) == \
        "//scaqaq01-scan1.us.oracle.com:1521/pdbpp_001"
    # prefix (26) + "_" + 3 digits fits Oracle's 30 characters
    assert len(pdb_name("A" * 26, 999)) == 30
