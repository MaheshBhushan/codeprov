"""Small idempotent JSON-record store; keys are scoped to a frozen run."""
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(kind, key)
            );
            CREATE TABLE IF NOT EXISTS stages (
                name TEXT PRIMARY KEY, status TEXT NOT NULL, details TEXT NOT NULL
            );
        """)

    def put(self, kind: str, key: str, data: dict):
        with self.connection:
            self.connection.execute(
                "INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(kind, key) DO UPDATE SET payload=excluded.payload",
                (kind, key, json.dumps(data, sort_keys=True, allow_nan=False)),
            )

    def rows(self, kind: str) -> list[dict]:
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT payload FROM records WHERE kind=? ORDER BY key", (kind,)
        )]

    def get(self, kind: str, key: str) -> dict | None:
        row = self.connection.execute("SELECT payload FROM records WHERE kind=? AND key=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def clear(self, kind: str):
        with self.connection:
            self.connection.execute("DELETE FROM records WHERE kind=?", (kind,))

    def stage(self, name: str, status: str, details: dict):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO stages VALUES (?, ?, ?)",
                                    (name, status, json.dumps(details, sort_keys=True)))

    def stages(self) -> list[dict]:
        return [{"name": n, "status": s, "details": json.loads(d)} for n, s, d in self.connection.execute(
            "SELECT name, status, details FROM stages ORDER BY name")]

    def close(self):
        self.connection.close()
