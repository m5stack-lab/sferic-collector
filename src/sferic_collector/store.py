"""Strike / Flash store. SQLite for now — Redis (hot state) + Qdrant
(semantic recall) slot in when the Pi 5 collector lands (ADR-0007 §3).

The full record is kept as JSON in `doc`; the promoted columns exist only
for filtering.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Flash, Strike

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strikes (
  id       TEXT PRIMARY KEY,
  station  TEXT NOT NULL,
  flash    TEXT,
  ts       TEXT NOT NULL,
  toolset  TEXT NOT NULL,
  kind     TEXT NOT NULL,
  label    TEXT,
  doc      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_strikes_station ON strikes(station);
CREATE INDEX IF NOT EXISTS ix_strikes_flash   ON strikes(flash);

CREATE TABLE IF NOT EXISTS flashes (
  id         TEXT PRIMARY KEY,
  station    TEXT NOT NULL,
  toolset    TEXT NOT NULL,
  label      TEXT,
  opened_at  TEXT NOT NULL,
  closed_at  TEXT,
  doc        TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path = "sferic.db") -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # --- strikes ---

    def add_strike(self, s: Strike) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO strikes"
            " (id, station, flash, ts, toolset, kind, label, doc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s.id, s.station, s.flash, s.ts, s.toolset, s.kind, s.label, s.model_dump_json()),
        )
        self.db.commit()

    def strikes(
        self, station: str | None = None, flash: str | None = None, limit: int = 50
    ) -> list[Strike]:
        clauses, args = [], []
        if station:
            clauses.append("station = ?")
            args.append(station)
        if flash:
            clauses.append("flash = ?")
            args.append(flash)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(max(1, min(limit, 1000)))
        rows = self.db.execute(
            f"SELECT doc FROM strikes{where} ORDER BY ts DESC, id DESC LIMIT ?", args
        )
        return [Strike.model_validate_json(r[0]) for r in rows]

    # --- flashes ---

    def add_flash(self, f: Flash) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO flashes"
            " (id, station, toolset, label, opened_at, closed_at, doc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f.id, f.station, f.toolset, f.label, f.opened_at, f.closed_at, f.model_dump_json()),
        )
        self.db.commit()

    def flashes(self, station: str | None = None) -> list[Flash]:
        where, args = ("", [])
        if station:
            where, args = (" WHERE f.station = ?", [station])
        rows = self.db.execute(
            "SELECT f.doc, COUNT(s.id) FROM flashes f"
            " LEFT JOIN strikes s ON s.flash = f.id"
            f"{where}"
            " GROUP BY f.id ORDER BY f.opened_at DESC",
            args,
        ).fetchall()
        out: list[Flash] = []
        for doc, count in rows:
            f = Flash.model_validate_json(doc)
            f.strike_count = count
            out.append(f)
        return out

    def close(self) -> None:
        self.db.close()
