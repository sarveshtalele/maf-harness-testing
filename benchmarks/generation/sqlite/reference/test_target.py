import sqlite3

import pytest

from target import initialize, record, summary


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "usage.db"
    initialize(path)
    return path


def test_source_defined_summary(database):
    record(database, "a.jsonl", 1, 10)
    record(database, "a.jsonl", 2, 5)
    assert summary(database) == {"total_tokens": 15}


def test_uniqueness_and_rollback(database):
    record(database, "a.jsonl", 1, 10)
    with pytest.raises(sqlite3.IntegrityError):
        record(database, "a.jsonl", 1, 99)
    record(database, "a.jsonl", 2, 5)
    assert summary(database) == {"total_tokens": 15}


def test_empty_database(database):
    assert summary(database) == {"total_tokens": 0}
