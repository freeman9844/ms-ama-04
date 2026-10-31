import pytest

from shared.pseudonymize import Pseudonymizer, find_pii

KEY = b"k" * 32
ENTITIES = {"김민수": "PERSON", "성수커피랩": "BIZNAME", "이서연": "PERSON"}


def test_masks_known_entities_and_restores():
    p = Pseudonymizer(KEY, ENTITIES)
    text = "성수커피랩 대표 김민수가 이서연에게 급여 이체"
    masked = p.mask(text)
    for value in ENTITIES:
        assert value not in masked
    assert "[[PERSON_" in masked and "[[BIZNAME_" in masked
    assert p.restore(masked) == text


def test_same_value_same_token_across_instances():
    a = Pseudonymizer(KEY, ENTITIES).mask("김민수")
    b = Pseudonymizer(KEY, ENTITIES).mask("김민수 입금")
    assert b.startswith(a)


def test_different_key_different_token():
    a = Pseudonymizer(KEY, ENTITIES).mask("김민수")
    b = Pseudonymizer(b"z" * 32, ENTITIES).mask("김민수")
    assert a != b


def test_overlapping_entities_longest_first():
    entities = {"김민수": "PERSON", "김민수커피": "BIZNAME"}
    p = Pseudonymizer(KEY, entities)
    masked = p.mask("김민수커피 대표 김민수")
    assert masked.startswith("[[BIZNAME_")
    assert masked.count("[[PERSON_") == 1
    assert "커피" not in masked
    assert p.restore(masked) == "김민수커피 대표 김민수"


def test_digits_adjacent_to_hangul():
    p = Pseudonymizer(KEY)
    masked = p.mask("사업자123-45-67890입니다 연락처010-1234-5678로")
    assert "123-45-67890" not in masked
    assert "010-1234-5678" not in masked
    assert "[[BIZNO_" in masked and "[[PHONE_" in masked
    assert masked.startswith("사업자[[BIZNO_")


def test_account_number_masked():
    p = Pseudonymizer(KEY)
    masked = p.mask("국민 123456-78-901234 이체")
    assert "123456-78-901234" not in masked
    assert "[[ACCOUNT_" in masked


def test_amounts_are_not_masked():
    p = Pseudonymizer(KEY)
    assert p.mask("카드매출 1,250,000원") == "카드매출 1,250,000원"


def test_mask_is_idempotent():
    p = Pseudonymizer(KEY, ENTITIES)
    once = p.mask("김민수 010-1234-5678 123-45-67890")
    assert p.mask(once) == once


def test_restore_leaves_unknown_tokens():
    p = Pseudonymizer(KEY, ENTITIES)
    assert p.restore("[[PERSON_0123456789]] 확인") == "[[PERSON_0123456789]] 확인"


def test_short_key_rejected():
    with pytest.raises(ValueError):
        Pseudonymizer(b"short")


def test_find_pii_detects_leak_and_clean_after_mask():
    text = "김민수 010-1234-5678"
    found = find_pii(text, ENTITIES)
    assert found == [("PERSON", "김민수"), ("PHONE", "010-1234-5678")]
    masked = Pseudonymizer(KEY, ENTITIES).mask(text)
    assert find_pii(masked, ENTITIES) == []
