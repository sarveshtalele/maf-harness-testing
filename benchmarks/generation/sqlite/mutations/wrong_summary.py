import sqlite3
from contextlib import closing


def connect(db_path):
    return sqlite3.connect(db_path)


def initialize(db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS usage (transcript_path TEXT, transcript_line INTEGER, tokens INTEGER NOT NULL, UNIQUE(transcript_path, transcript_line))")


def record(db_path, path, line, tokens):
    with closing(connect(db_path)) as db, db:
        db.execute("INSERT INTO usage VALUES (?, ?, ?)", (path, line, tokens))


def summary(db_path):
    with closing(connect(db_path)) as db:
        return {"total_cost": db.execute("SELECT COALESCE(SUM(tokens), 0) FROM usage").fetchone()[0]}
