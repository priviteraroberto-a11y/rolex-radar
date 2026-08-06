"""Popola un database di esempio e genera la dashboard, senza toccare la rete.

    python tools/demo.py

Serve per (a) vedere com'è fatta la dashboard prima di collegare le fonti vere
e (b) capire come il punteggio tratta annunci diversi fra loro.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import dashboard                      # noqa: E402
from radar.config import Config                  # noqa: E402
from radar.db import Database                    # noqa: E402
from radar.fairvalue import FairValueEngine      # noqa: E402
from radar.models import Listing                 # noqa: E402
from radar.scorer import Scorer, stars           # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

DEMO = [
    # (fonte, anno, condizione, bracciale, full_set, garanzia, mai_lucidato, prezzo, trust)
    ("davidepedretti",   2025, "unworn",    "jubilee", True,  "IT", True,  33900, 5),
    ("pluswatch",        2024, "mint",      "jubilee", True,  "IT", True,  31200, 4),
    ("dellarocca",       2025, "unworn",    "jubilee", True,  "EU", True,  34800, 4),
    ("chrono24",         2023, "excellent", "jubilee", False, "AE", False, 25900, 2),
    ("chrono24",         2026, "unworn",    "jubilee", True,  "EU", True,  38500, 3),
    ("orologiepassioni", 2024, "like_new",  "oyster",  True,  "IT", True,  29400, 3),
    ("watchfinder",      2022, "very_good", "jubilee", False, "UK", False, 26800, 4),
    ("chrono24",         2025, "unworn",    "jubilee", True,  "SA", True,  30100, 2),
    ("wristler",         2024, "mint",      "jubilee", True,  "CH", None,  32400, 3),
    ("pluswatch",        2023, "mint",      "jubilee", True,  "IT", True,  28600, 4),
    ("chrono24",         2019, "good",      "oyster",  False, "JP", False, 23200, 2),
    ("dellarocca",       2026, "unworn",    "jubilee", True,  "IT", True,  39900, 4),
]


def main() -> int:
    cfg = Config.load(ROOT / "config.yaml")
    db_path = ROOT / "demo.db"
    db_path.unlink(missing_ok=True)
    db = Database(db_path)

    listings = [
        Listing(
            source=src, url=f"https://esempio.it/{src}/{i}",
            title=f"Rolex GMT-Master II 126710BLRO Pepsi {brac} {year}",
            reference="126710BLRO", year=year, condition=cond, bracelet=brac,
            full_set=fs, warranty_region=war, never_polished=np_,
            price_eur=float(price), seller_trust=trust,
        )
        for i, (src, year, cond, brac, fs, war, np_, price, trust) in enumerate(DEMO)
    ]

    comps = [
        {"price_eur": l.price_eur, "year": l.year, "condition": l.condition,
         "bracelet": l.bracelet, "full_set": int(bool(l.full_set)),
         "warranty_region": l.warranty_region,
         "never_polished": None if l.never_polished is None else int(l.never_polished)}
        for l in listings
    ]
    engine = FairValueEngine(cfg, comps)
    scorer = Scorer(cfg)

    for l in listings:
        engine.evaluate(l)
        scorer.score(l)
        db.upsert(l)

    # storico di mercato finto, per far vedere il grafico
    rng = random.Random(7)
    base = engine.index
    for d in range(45, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=d)).date().isoformat()
        drift = base * (1 + 0.0012 * (45 - d)) + rng.uniform(-320, 320)
        db.conn.execute(
            """INSERT OR REPLACE INTO market_snapshots
               (ts, reference, n_listings, median_eur, p25_eur, index_value)
               VALUES (?,?,?,?,?,?)""",
            (day, "126710BLRO", len(listings), round(drift), round(drift * 0.93),
             round(drift)),
        )
    db.conn.commit()

    m = engine.summary()
    print(f"\n  Indice di mercato stimato: {m['index']:,.0f} €  "
          f"({m['samples']} campioni, "
          f"{'data-driven' if m['data_driven'] else 'seed'})\n")
    print(f"  {'SCORE':>6}  {'PREZZO':>9}  {'STIMA':>9}  {'SCARTO':>9}  FONTE / PROFILO")
    print("  " + "─" * 84)
    for l in sorted(listings, key=lambda x: -x.score):
        print(f"  {l.score:>3}/100 {stars(l.score)}  {l.price_eur:>8,.0f}€  "
              f"{l.fair_value_eur:>8,.0f}€  {l.delta_eur:>+8,.0f}€  "
              f"{l.source:<17} {l.year} {l.condition} {l.bracelet} "
              f"gar.{l.warranty_region}")

    out = dashboard.build(db, m, ROOT / "docs" / "index.html")
    print(f"\n  Dashboard → {out}\n")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
