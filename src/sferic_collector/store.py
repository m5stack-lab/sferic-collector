"""Strike / Flash store + recall index. SQLite for now — Redis (hot state)
slots in when the Pi 5 collector lands (ADR-0007 §3).

The full record is kept as JSON in `doc`; the promoted columns exist only
for filtering. `Store.search()` is a local full-text recall over a flattened
projection of each strike (`models.strike_text`); a semantic (embedding)
backend can replace it behind the same signature for the Pi phase.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Flash, Strike, strike_text

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


def _fts_query(query: str) -> str:
    """User text -> a safe FTS5 MATCH expression: each term a quoted literal,
    AND-combined. Keeps punctuation in the query from breaking the parser."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if t]
    return " AND ".join(f'"{t}"' for t in terms)


class Store:
    def __init__(self, path: str | Path = "sferic.db") -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self._fts = self._init_fts()
        self.db.commit()

    def _init_fts(self) -> bool:
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS strikes_fts USING fts5("
                " id UNINDEXED, text, tokenize='porter unicode61')"
            )
            return True
        except sqlite3.OperationalError:
            return False  # sqlite built without FTS5 — search() falls back to LIKE

    # --- strikes ---

    def add_strike(self, s: Strike) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO strikes"
            " (id, station, flash, ts, toolset, kind, label, doc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s.id, s.station, s.flash, s.ts, s.toolset, s.kind, s.label, s.model_dump_json()),
        )
        if self._fts:
            self.db.execute("DELETE FROM strikes_fts WHERE id = ?", (s.id,))
            self.db.execute(
                "INSERT INTO strikes_fts (id, text) VALUES (?, ?)", (s.id, strike_text(s))
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

    def search(
        self,
        query: str,
        station: str | None = None,
        toolset: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[Strike]:
        """Recall: strikes whose flattened text best matches `query`, ranked
        best-first. Optional exact filters on station / toolset / kind."""
        limit = max(1, min(limit, 200))
        filt, fargs = [], []
        for col, val in (("station", station), ("toolset", toolset), ("kind", kind)):
            if val:
                filt.append(f"s.{col} = ?")
                fargs.append(val)
        filt_sql = (" AND " + " AND ".join(filt)) if filt else ""

        if self._fts:
            match = _fts_query(query)
            if not match:
                return []
            rows = self.db.execute(
                "SELECT s.doc FROM strikes_fts f JOIN strikes s ON s.id = f.id"
                f" WHERE strikes_fts MATCH ?{filt_sql}"
                " ORDER BY bm25(strikes_fts) LIMIT ?",
                [match, *fargs, limit],
            )
        else:
            terms = [t for t in query.split() if t]
            like = " AND ".join("s.doc LIKE ?" for _ in terms) or "1"
            rows = self.db.execute(
                f"SELECT s.doc FROM strikes s WHERE {like}{filt_sql} ORDER BY s.ts DESC LIMIT ?",
                [*(f"%{t}%" for t in terms), *fargs, limit],
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
            where, args = (" WHERE station = ?", [station])
        docs = self.db.execute(
            f"SELECT doc FROM flashes{where} ORDER BY opened_at DESC", args
        ).fetchall()
        out: list[Flash] = []
        for (doc,) in docs:
            f = Flash.model_validate_json(doc)
            (f.strike_count,) = self.db.execute(
                "SELECT COUNT(*) FROM strikes WHERE flash = ?", (f.id,)
            ).fetchone()
            out.append(f)
        return out

    def close(self) -> None:
        self.db.close()
