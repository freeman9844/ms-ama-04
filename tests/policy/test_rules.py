import pytest
import yaml
from pydantic import ValidationError

from policy.rules import DEFAULT_RULES_PATH, RuleSet, load_rules, rules_for_version


def test_rules_for_version_reads_every_ruleset():
    for v in ("2026.11-v1", "2026.11-v2", "2026.11-v3", "2026.11-v4"):
        assert rules_for_version(v).version == v
    assert rules_for_version("2026.10-v0") is None and rules_for_version(None) is None


def _data():
    return yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def test_default_rules_load(rules):
    assert rules.version == "2026.11-v4"
    assert [c.code for c in rules.knockouts] == ["R001", "R002", "R003"]
    assert [c.code for c in rules.refers] == ["R102", "R103", "R104", "R105", "R106", "R107", "R108", "R109", "R110"]
    assert [b.band for b in rules.rate_bands] == ["A", "B", "C", "D"]


def test_default_is_v4():
    r = load_rules()
    assert r.version == "2026.11-v4"
    assert [s.code for s in r.substitutions] == ["S001", "S002"]
    assert r.limit_boost.pending_settlement_ratio == 0.8
    assert r.limit_boost.substituted_max_amount_krw == 5_000_000


def test_v1_to_v3_load_without_substitutions():
    for name in ("v1.yaml", "v2.yaml", "v3.yaml"):
        r = load_rules(DEFAULT_RULES_PATH.with_name(name))
        assert r.substitutions == () and r.limit_boost is None


def test_v4_keeps_every_v3_rule():
    v3 = load_rules(DEFAULT_RULES_PATH.with_name("v3.yaml"))
    v4 = load_rules()
    assert v4.product == v3.product and v4.rate_bands == v3.rate_bands
    assert set(v3.knockouts) <= set(v4.knockouts) and set(v3.refers) <= set(v4.refers)


@pytest.mark.parametrize("target", ["R002", "R003", "R900", "R901"])
def test_rejects_non_substitutable(target):
    data = _data()
    data["substitutions"][0]["replaces"] = target
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_rejects_knockout_substitution_with_pass():
    data = _data()
    data["substitutions"][0]["outcome"] = "pass"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_rejects_unknown_replaces():
    data = _data()
    data["substitutions"][0]["replaces"] = "R999"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_rejects_two_substitutions_for_one_rule():
    data = _data()
    data["substitutions"][1]["replaces"] = data["substitutions"][0]["replaces"]
    data["substitutions"][1]["outcome"] = "refer"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_rejects_duplicate_substitution_code():
    data = _data()
    data["substitutions"][1]["code"] = "S001"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_v2_only_raises_product_max_over_v1():
    v1 = load_rules(DEFAULT_RULES_PATH.with_name("v1.yaml"))
    v2 = load_rules(DEFAULT_RULES_PATH.with_name("v2.yaml"))
    assert (v1.product.max_amount_krw, v2.product.max_amount_krw) == (5_000_000, 10_000_000)
    same = {"version", "product"}
    assert v1.model_dump(exclude=same) == v2.model_dump(exclude=same)
    assert v1.product.model_dump(exclude={"max_amount_krw"}) == v2.product.model_dump(exclude={"max_amount_krw"})


def test_duplicate_codes_rejected():
    data = _data()
    data["refers"][0]["code"] = "R001"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_reserved_code_rejected():
    data = _data()
    data["refers"][0]["code"] = "R900"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_rate_bands_must_strictly_descend():
    data = _data()
    data["rate_bands"][1]["min_dscr"] = 2.0
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_unknown_field_rejected():
    data = _data()
    data["refers"][0]["field"] = "credit_score"
    with pytest.raises(ValidationError):
        RuleSet.model_validate(data)


def test_load_rules_from_custom_path(tmp_path):
    data = _data()
    data["version"] = "test-v2"
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert load_rules(path).version == "test-v2"
