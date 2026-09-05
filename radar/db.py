"""Persistenza: storico prezzi, deduplica notifiche, serie storiche di mercato."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Listing

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key             TEXT PRIMARY KEY,
    watch_id        TEXT,
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
    seller_country  TEXT,
    seller_trust    INTEGER DEFAULT 0,
    image           TEXT,
    price_eur       REAL,
    fair_value_eur  REAL,
    delta_pct       REAL,
    delta_listino_pct REAL,
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
    ts          TEXT NOT NULL,
    watch_id    TEXT NOT NULL DEFAULT '',
    reference   TEXT,
    n_listings  INTEGER,
    median_eur  REAL,
    p25_eur     REAL,
    index_value REAL,
    PRIMARY KEY (ts, watch_id)
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


DEFAULT_WATCH_ID = "pepsi"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = "history.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Aggiunge le colonne nuove ai database gia' esistenti.

        Serve a non buttare via lo storico quando il sistema evolve: il
        database del Pepsi vive da prima che esistesse il multi-orologio.
        """
        cur = self.conn.execute("PRAGMA table_info(listings)")
        cols = {r[1] for r in cur.fetchall()}
        if "watch_id" not in cols:
            self.conn.execute("ALTER TABLE listings ADD COLUMN watch_id TEXT")
            # gli annunci storici sono tutti del primo orologio monitorato
            self.conn.execute(
                "UPDATE listings SET watch_id = ? WHERE watch_id IS NULL",
                (DEFAULT_WATCH_ID,))
        if "seller_country" not in cols:
            self.conn.execute("ALTER TABLE listings ADD COLUMN seller_country TEXT")
        if "delta_listino_pct" not in cols:
            # Il confronto col listino: calcolato a ogni giro ma, finche' non
            # esisteva questa colonna, buttato via subito dopo. La dashboard
            # legge dal database, non dalla memoria del giro.
            self.conn.execute("ALTER TABLE listings ADD COLUMN delta_listino_pct REAL")
        self._butta_i_non_annunci()
        cur = self.conn.execute("PRAGMA table_info(market_snapshots)")
        cols = {r[1] for r in cur.fetchall()}
        if "watch_id" not in cols:
            self.conn.execute(
                "ALTER TABLE market_snapshots ADD COLUMN watch_id TEXT DEFAULT ''")

    def _butta_i_non_annunci(self) -> None:
        """Toglie le righe che non erano annunci.

        Fino al 19/08/2026 il lettore delle email prendeva per annuncio anche
        i bottoni di servizio dei marketplace, le vetrine dei negozi e
        perfino i link alle foto ingrandite, abbinandoci il prezzo
        dell'annuncio accanto. Il risultato era un affare inesistente in
        dashboard — un Royal Oak a 20.400 euro che non esisteva. I lettori
        ora li scartano, ma quelli gia' salvati restano finche' non li si
        toglie di mezzo.
        """
        from .extract import e_url_di_annuncio
        cattive = [r["key"] for r in self.conn.execute(
                       "SELECT key, url FROM listings").fetchall()
                   if not e_url_di_annuncio(r["url"])]
        for chiave in cattive:
            self.conn.execute("DELETE FROM listings WHERE key = ?", (chiave,))
        n = len(cattive)
        if n:
            log.info("tolti %d falsi annunci dal database (link di servizio)", n)

    def close_unmonitored(self, monitorati: set[str]) -> int:
        """Mette a riposo gli annunci di orologi tolti dal config.

        Quando hai smesso di cercare il Pepsi, i suoi trentasette annunci sono
        rimasti marcati come attivi per sempre: nessun giro li guardava piu',
        quindi nessuno poteva chiuderli. Restano nel database — lo storico dei
        prezzi e' la cosa piu' preziosa che abbiamo — ma smettono di contare
        come "in vendita adesso".
        """
        if not monitorati:
            return 0
        segnaposto = ",".join("?" * len(monitorati))
        cur = self.conn.execute(
            f"UPDATE listings SET active = 0 "
            f"WHERE active = 1 AND (watch_id IS NULL OR watch_id NOT IN ({segnaposto}))",
            tuple(monitorati))
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()

    # -- lettura --------------------------------------------------------------

    def get(self, key: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM listings WHERE key = ?", (key,))
        return cur.fetchone()

    def comparables(self, watch_id: str, lookback_days: int) -> list[dict]:
        """Annunci recenti usati per stimare il valore di mercato.

        Il raggruppamento e' per **orologio**, non per referenza. Sembra una
        sfumatura e non lo e': un orologio riconosciuto per nome — lo Zenith
        Elite, lo Snoopy — non ha referenze, quindi con il vecchio criterio
        non poteva avere nemmeno un comparabile, e il suo indice restava per
        sempre la stima scritta a mano. Il legame annuncio-orologio lo ha
        gia' deciso il filtro a monte: e' li' che va letto.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        rows = self.conn.execute(
            """SELECT * FROM listings
               WHERE watch_id = ? AND price_eur IS NOT NULL AND last_seen >= ?""",
            (watch_id, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def active_listings(self, watch_id: str | None = None) -> list[dict]:
        if watch_id is None:
            rows = self.conn.execute(
                "SELECT * FROM listings WHERE active = 1 "
                "ORDER BY score DESC, price_eur ASC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM listings WHERE active = 1 AND watch_id = ? "
                "ORDER BY score DESC, price_eur ASC", (watch_id,)).fetchall()
        return [dict(r) for r in rows]

    def price_series(self, key: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT ts, price_eur FROM price_history WHERE key = ? ORDER BY ts", (key,)
        ).fetchall()
        return [(r["ts"], r["price_eur"]) for r in rows]

    def market_series(self, watch_id: str | None = None, limit: int = 180) -> list[dict]:
        if watch_id is None:
            rows = self.conn.execute(
                "SELECT * FROM market_snapshots ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM market_snapshots WHERE watch_id = ? "
                "ORDER BY ts DESC LIMIT ?", (watch_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent_notifications(self, hours: int = 24) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM notifications WHERE ts >= ? ORDER BY ts DESC", (since,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- scrittura ------------------------------------------------------------

    def upsert(self, listing: Listing, watch_id: str = DEFAULT_WATCH_ID) -> dict:
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
            listing.key, watch_id, listing.source, listing.url, listing.title,
            listing.reference, listing.year, listing.bracelet, listing.condition,
            _b(listing.full_set), _b(listing.never_polished), listing.warranty_region,
            listing.seller, listing.seller_country, listing.seller_trust, listing.image,
            listing.price_eur, listing.fair_value_eur, listing.delta_pct,
            listing.delta_listino_pct, listing.score, now, now, payload,
        )

        if prev is None:
            self.conn.execute(
                """INSERT INTO listings
                   (key, watch_id, source, url, title, reference, year, bracelet,
                    condition, full_set, never_polished, warranty_region, seller,
                    seller_country, seller_trust, image, price_eur, fair_value_eur,
                    delta_pct, delta_listino_pct, score, first_seen, last_seen,
                    payload, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                values,
            )
        else:
            self.conn.execute(
                """UPDATE listings SET
                     watch_id=?, source=?, url=?, title=?, reference=?, year=?, bracelet=?,
                     condition=?, full_set=?, never_polished=?, warranty_region=?,
                     seller=?, seller_country=?, seller_trust=?, image=?, price_eur=?,
                     fair_value_eur=?, delta_pct=?, delta_listino_pct=?, score=?,
                     last_seen=?, payload=?, active=1
                   WHERE key=?""",
                (*values[1:21], now, payload, listing.key),
            )

        # storico prezzi: una riga al giorno per annuncio
        day = now[:10]
        self.conn.execute(
            "INSERT OR REPLACE INTO price_history (key, ts, price_eur, score) VALUES (?,?,?,?)",
            (listing.key, day, listing.price_eur, listing.score),
        )
        self.conn.commit()
        return change

    def mark_inactive_except(self, seen_keys: Iterable[str], sources: Iterable[str],
                             watch_id: str | None = None) -> int:
        """Chiude gli annunci non più visti.

        Limitato alle fonti che hanno risposto E all'orologio appena
        controllato: senza il vincolo sull'orologio, un giro che ne controlla
        solo alcuni chiuderebbe gli annunci di tutti gli altri.
        """
        seen = list(seen_keys)
        srcs = list(sources)
        if not srcs:
            return 0
        sp = ",".join("?" for _ in srcs)
        kp = ",".join("?" for _ in seen) or "''"
        extra, params = "", []
        if watch_id is not None:
            extra = " AND watch_id = ?"
            params = [watch_id]
        cur = self.conn.execute(
            f"UPDATE listings SET active = 0 "
            f"WHERE active = 1 AND source IN ({sp}){extra} AND key NOT IN ({kp})",
            (*srcs, *params, *seen),
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
                             p25: float | None, index_value: float | None,
                             watch_id: str = DEFAULT_WATCH_ID) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO market_snapshots
               (ts, watch_id, reference, n_listings, median_eur, p25_eur, index_value)
               VALUES (?,?,?,?,?,?,?)""",
            (_now()[:10], watch_id, reference, n, median, p25, index_value),
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
