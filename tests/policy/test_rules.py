import pytest
import yaml
from pydantic import ValidationError

from policy.rules import DEFAULT_RULES_PATH, RuleSet, load_rules


def _data():
    return yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))


def test_default_rules_load(rules):
    assert rules.version == "2026.11-v1"
    assert [c.code for c in rules.knockouts] == ["R001", "R002"]
    assert [c.code for c in rules.refers] == ["R101", "R102", "R103", "R104"]
    assert [b.band for b in rules.rate_bands] == ["A", "B", "C", "D"]


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
