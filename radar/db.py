"""Persistenza: storico prezzi, deduplica notifiche, serie storiche di mercato."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key             TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT,
    reference       TEXT,
    year            INTEGER,
    bracelet        TEXT,
    condition       TEXT,
    full_set        INTEGER,
    never_polished  INTEGER,
    warranty_region TEXT,
    seller          TEXT,
    seller_trust    INTEGER DEFAULT 0,
    image           TEXT,
    price_eur       REAL,
    fair_value_eur  REAL,
    delta_pct       REAL,
    score           INTEGER DEFAULT 0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    active          INTEGER DEFAULT 1,
    payload         TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    key       TEXT NOT NULL,
    ts        TEXT NOT NULL,
    price_eur REAL,
    score     INTEGER,
    PRIMARY KEY (key, ts)
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key       TEXT NOT NULL,
    ts        TEXT NOT NULL,
    reason    TEXT,
    price_eur REAL,
    score     INTEGER
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    ts          TEXT PRIMARY KEY,
    reference   TEXT,
    n_listings  INTEGER,
    median_eur  REAL,
    p25_eur     REAL,
    index_value REAL
);

CREATE TABLE IF NOT EXISTS run_log (
    ts       TEXT PRIMARY KEY,
    source   TEXT,
    ok       INTEGER,
    n_items  INTEGER,
    detail   TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(active, score);
CREATE INDEX IF NOT EXISTS idx_price_history_ts ON price_history(ts);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = "history.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- lettura --------------------------------------------------------------

    def get(self, key: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM listings WHERE key = ?", (key,))
        return cur.fetchone()

    def comparables(self, references: list[str], lookback_days: int) -> list[dict]:
        """Annunci recenti usati per stimare il valore di mercato."""
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        placeholders = ",".join("?" for _ in references) or "''"
        rows = self.conn.execute(
            f"""SELECT * FROM listings
                WHERE reference IN ({placeholders})
                  AND price_eur IS NOT NULL
                  AND last_seen >= ?
            """,
            (*references, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def active_listings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM listings WHERE active = 1 ORDER BY score DESC, price_eur ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def price_series(self, key: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT ts, price_eur FROM price_history WHERE key = ? ORDER BY ts", (key,)
        ).fetchall()
        return [(r["ts"], r["price_eur"]) for r in rows]

    def market_series(self, limit: int = 180) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM market_snapshots ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent_notifications(self, hours: int = 24) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM notifications WHERE ts >= ? ORDER BY ts DESC", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- scrittura ------------------------------------------------------------

    def upsert(self, listing: Listing) -> dict:
        """Inserisce o aggiorna. Ritorna il delta rispetto allo stato precedente."""
        now = _now()
        prev = self.get(listing.key)
        change = {
            "is_new": prev is None,
            "old_price": prev["price_eur"] if prev else None,
            "old_score": prev["score"] if prev else None,
        }

        payload = json.dumps(listing.to_dict(), ensure_ascii=False, default=str)
        values = (
            listing.key, listing.source, listing.url, listing.title,
            listing.reference, listing.year, listing.bracelet, listing.condition,
            _b(listing.full_set), _b(listing.never_polished), listing.warranty_region,
            listing.seller, listing.seller_trust, listing.image,
            listing.price_eur, listing.fair_value_eur, listing.delta_pct,
            listing.score, now, now, payload,
        )

        if prev is None:
            self.conn.execute(
                """INSERT INTO listings
                   (key, source, url, title, reference, year, bracelet, condition,
                    full_set, never_polished, warranty_region, seller, seller_trust,
                    image, price_eur, fair_value_eur, delta_pct, score,
                    first_seen, last_seen, payload, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                values,
            )
        else:
            self.conn.execute(
                """UPDATE listings SET
                     source=?, url=?, title=?, reference=?, year=?, bracelet=?,
                     condition=?, full_set=?, never_polished=?, warranty_region=?,
                     seller=?, seller_trust=?, image=?, price_eur=?, fair_value_eur=?,
                     delta_pct=?, score=?, last_seen=?, payload=?, active=1
                   WHERE key=?""",
                (*values[1:18], now, payload, listing.key),
            )

        # storico prezzi: una riga al giorno per annuncio
        day = now[:10]
        self.conn.execute(
            "INSERT OR REPLACE INTO price_history (key, ts, price_eur, score) VALUES (?,?,?,?)",
            (listing.key, day, listing.price_eur, listing.score),
        )
        self.conn.commit()
        return change

    def mark_inactive_except(self, seen_keys: Iterable[str], sources: Iterable[str]) -> int:
        """Chiude gli annunci non più visti, ma solo per le fonti che hanno risposto."""
        seen = list(seen_keys)
        srcs = list(sources)
        if not srcs:
            return 0
        sp = ",".join("?" for _ in srcs)
        kp = ",".join("?" for _ in seen) or "''"
        cur = self.conn.execute(
            f"UPDATE listings SET active = 0 "
            f"WHERE active = 1 AND source IN ({sp}) AND key NOT IN ({kp})",
            (*srcs, *seen),
        )
        self.conn.commit()
        return cur.rowcount

    def log_notification(self, key: str, reason: str, price: float | None, score: int) -> None:
        self.conn.execute(
            "INSERT INTO notifications (key, ts, reason, price_eur, score) VALUES (?,?,?,?,?)",
            (key, _now(), reason, price, score),
        )
        self.conn.commit()

    def save_market_snapshot(self, reference: str, n: int, median: float | None,
                             p25: float | None, index_value: float | None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO market_snapshots
               (ts, reference, n_listings, median_eur, p25_eur, index_value)
               VALUES (?,?,?,?,?,?)""",
            (_now()[:10], reference, n, median, p25, index_value),
        )
        self.conn.commit()

    def log_run(self, source: str, ok: bool, n_items: int, detail: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO run_log (ts, source, ok, n_items, detail) VALUES (?,?,?,?,?)",
            (f"{_now()}|{source}", source, int(ok), n_items, detail[:500]),
        )
        self.conn.commit()


def _b(v: Optional[bool]) -> Optional[int]:
    return None if v is None else int(v)
