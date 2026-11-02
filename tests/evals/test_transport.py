import random

import pytest

from evals.transport import TransportError, chunks, reassemble

DATA = random.Random(1).randbytes(6000)


def test_round_trip_in_any_order_with_duplicates_and_log_prefix():
    lines = chunks("run-1", "report.md", DATA, piece=500)
    assert len(lines) > 2
    noisy = [f"2026-11-01T00:00:00Z stdout {line}" for line in lines + lines[:2]] + ["unrelated", *chunks("run-2", "x", b"x")]
    random.Random(7).shuffle(noisy)
    assert reassemble(noisy, "run-1") == {"report.md": DATA}


def test_missing_piece_is_an_error():
    lines = chunks("run-1", "report.md", DATA, piece=500)
    with pytest.raises(TransportError, match="누락"):
        reassemble(lines[:-1], "run-1")


def test_corrupted_piece_fails_hash_check():
    lines = chunks("run-1", "report.md", DATA, piece=500)
    head, body = lines[0].rsplit(" ", 1)
    lines[0] = f"{head} {'A' * len(body)}"
    with pytest.raises(TransportError):
        reassemble(lines, "run-1")


def test_chunks_are_deterministic():
    assert chunks("r", "f", DATA) == chunks("r", "f", DATA)
