"""Persist only public, already-cleaned run records across service restarts."""
import json
import re
import sqlite3
from pathlib import Path
from .config import ROOT


def connect(path=None):
    target = Path(path) if path else ROOT/'runtime/records/runs.sqlite3'
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at REAL DEFAULT (unixepoch()))')
    return conn


def save_run(identifier, record, path=None):
    with connect(path) as conn:
        conn.execute('INSERT OR REPLACE INTO runs(id,body) VALUES(?,?)',
                     (identifier, json.dumps(record, ensure_ascii=False)))
        conn.execute('DELETE FROM runs WHERE id IN (SELECT id FROM runs ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET 200)')


def load_run(identifier, path=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', identifier):
        return None
    with connect(path) as conn:
        row = conn.execute('SELECT body FROM runs WHERE id=?', (identifier,)).fetchone()
    return json.loads(row[0]) if row else None
