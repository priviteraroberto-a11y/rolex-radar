"""Orchestratore.

Comandi:
    python -m radar.main check       # giro completo (raccolta, score, notifiche)
    python -m radar.main check --dry-run
    python -m radar.main probe       # diagnostica: quali fonti rispondono?
    python -m radar.main dashboard   # rigenera solo docs/index.html
    python -m radar.main test-notify # invia una notifica di prova
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Any, Optional

from . import dashboard as dash
from . import extract, sources
from .config import Config
from .db import Database
from .fairvalue import FairValueEngine
from .fetch import Fetcher
from .models import Listing
from .notify import EmailNotifier, TelegramNotifier, decide_notifications
from .scorer import Scorer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-14s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("radar")


@dataclass
class Context:
    config: Config
    fetcher: Fetcher


# =============================================================================
# raccolta
# =============================================================================

def collect(cfg: Config, ctx: Context) -> tuple[list[Listing], list[str]]:
    """Ritorna (annunci grezzi, nomi delle fonti che hanno risposto)."""
    all_listings: list[Listing] = []
    healthy: list[str] = []

    for src_cfg in cfg.sources:
        name = src_cfg.get("name", "?")
        try:
            source = sources.build(src_cfg, ctx)
            result = source.collect()
        except Exception as exc:
            log.error("[%s] eccezione: %s: %s", name, type(exc).__name__, exc)
            continue

        if result.ok:
            healthy.append(name)
            log.info("[%s] %d annunci — %s", name, len(result.listings), result.detail)
        else:
            log.warning("[%s] FONTE NON RAGGIUNGIBILE — %s", name, result.detail)

        for l in result.listings:
            extract.enrich(l, src_cfg)
        all_listings.extend(result.listings)

    return all_listings, healthy


def reject_reason(l: Listing, cfg: Config) -> Optional[str]:
    """Perché questo annuncio non ci interessa? None = va tenuto.

    Tenere la logica di scarto in un posto solo serve a due cose: evitare che
    `check` e `inspect` divergano, e poter spiegare all'utente cosa è successo.
    """
    wanted = cfg.references
    keywords = cfg.get("watch.model_keywords", [])
    excluded = cfg.get("watch.exclude_keywords", [])
    hf = cfg.get("hard_filters", {})
    lo = float(hf.get("absolute_min_price_eur", 0))
    hi = float(hf.get("absolute_max_price_eur", 10**9))
    ymin, ymax = int(hf.get("min_year", 1900)), int(hf.get("max_year", 2100))

    text = f"{l.title} {l.raw_text}"

    if not extract.matches_reference(l.reference, text, wanted):
        return "referenza assente dal testo"
    if not extract.is_target_watch(l.title, text, wanted, keywords, excluded):
        low = extract.norm(l.title)
        for k in excluded:
            if extract.norm(k) in low:
                return f"modello escluso: {k}"
        if extract.other_reference_in_title(l.title, wanted):
            return "il titolo cita un'altra referenza"
        return "referenza solo nel corpo e modello assente dal titolo"
    if bool(hf.get("exclude_sold", True)) and l.sold:
        return "gia venduto / non disponibile"
    if l.price_eur is not None and not (lo <= l.price_eur <= hi):
        return f"prezzo fuori range ({l.price_eur:,.0f} EUR)"
    if l.year is not None and not (ymin <= l.year <= ymax):
        return f"anno fuori range ({l.year})"
    return None


def filter_relevant(listings: list[Listing], cfg: Config) -> list[Listing]:
    """L'unico filtro duro: la referenza. Più i limiti di sanità sul prezzo."""
    out, seen = [], set()
    for l in listings:
        reason = reject_reason(l, cfg)
        if reason:
            log.debug("scartato (%s): %s", reason, l.url)
            continue
        if l.key in seen:
            continue
        seen.add(l.key)
        out.append(l)
    return out


# =============================================================================
# comandi
# =============================================================================

def cmd_check(args) -> int:
    cfg = Config.load(args.config)
    ctx = Context(cfg, Fetcher(cfg.get("http", {})))
    db = Database(args.db)

    log.info("── raccolta ──────────────────────────────────────────")
    raw, healthy = collect(cfg, ctx)
    listings = filter_relevant(raw, cfg)
    log.info("%d annunci grezzi → %d pertinenti", len(raw), len(listings))

    # Il fair value si basa sullo storico + su ciò che vediamo adesso.
    comparables = db.comparables(cfg.references, int(cfg.get("fair_value.lookback_days", 60)))
    comparables += [
        {"price_eur": l.price_eur, "year": l.year, "condition": l.condition,
         "bracelet": l.bracelet, "full_set": _i(l.full_set),
         "warranty_region": l.warranty_region, "never_polished": _i(l.never_polished)}
        for l in listings if l.price_eur
    ]
    engine = FairValueEngine(cfg, comparables)
    market = engine.summary()
    log.info("── mercato ───────────────────────────────────────────")
    log.info("indice %s € su %d campioni (%s)", f"{market['index']:,.0f}",
             market["samples"],
             "data-driven" if market["data_driven"] else "stima di partenza")

    scorer = Scorer(cfg)
    scored: list[tuple[Listing, dict]] = []
    for l in listings:
        engine.evaluate(l)
        scorer.score(l)
        change = db.upsert(l) if not args.dry_run else {"is_new": True, "old_price": None,
                                                        "old_score": 0}
        scored.append((l, change))

    scored.sort(key=lambda t: -t[0].score)
    for l, _ in scored[:10]:
        log.info("  %3d/100  %9s  %s  %s", l.score,
                 f"{l.price_eur:,.0f}€" if l.price_eur else "n/d",
                 f"{l.year or '????'}", l.url[:70])

    if not args.dry_run:
        closed = db.mark_inactive_except([l.key for l in listings], healthy)
        if closed:
            log.info("%d annunci non più online", closed)
        db.save_market_snapshot(
            cfg.references[0] if cfg.references else "?",
            market["samples"], market["median_raw"], market["p25"], market["index"],
        )
        for name in healthy:
            db.log_run(name, True, len(listings))

    log.info("── notifiche ─────────────────────────────────────────")
    decisions = decide_notifications(scored, cfg, engine.is_underpriced, force=args.force)
    log.info("%d notifiche da inviare", len(decisions))

    if args.dry_run:
        for d in decisions:
            log.info("  [DRY] %-12s %3d/100  %s", d.reason, d.listing.score, d.headline)
    else:
        # Un problema di notifica non deve mai far fallire il giro: lo storico
        # e la dashboard valgono comunque, e un run fallito significa DB non
        # salvato, quindi notifiche duplicate al giro successivo.
        try:
            tg = TelegramNotifier()
            for d in decisions:
                if tg.send(d):
                    db.log_notification(d.listing.key, d.reason, d.listing.price_eur,
                                        d.listing.score)
        except Exception as exc:
            log.error("invio Telegram fallito: %s: %s", type(exc).__name__, exc)
        try:
            if decisions:
                EmailNotifier().send_digest(decisions, market)
        except Exception as exc:
            log.error("invio email fallito: %s: %s", type(exc).__name__, exc)

    path = dash.build(db, market, args.dashboard)
    log.info("dashboard → %s", path)
    db.close()
    return 0


def cmd_probe(args) -> int:
    """Diagnostica: dice quali fonti sono realmente utilizzabili."""
    cfg = Config.load(args.config)
    ctx = Context(cfg, Fetcher(cfg.get("http", {})))

    print("\n  FONTE                 STATO         ANNUNCI  DETTAGLIO")
    print("  " + "─" * 106)
    usable = 0
    for src_cfg in cfg.all_sources:
        name = src_cfg.get("name", "?")
        if not src_cfg.get("enabled"):
            print(f"  {name:<21} {'spenta':<13} {'—':>7}  enabled: false")
            continue
        try:
            result = sources.build(src_cfg, ctx).collect()
        except Exception as exc:
            print(f"  {name:<21} {'ERRORE':<13} {'—':>7}  {type(exc).__name__}: {exc}")
            continue

        relevant = filter_relevant(
            [extract.enrich(l, src_cfg) for l in result.listings], cfg
        )
        if result.ok and relevant:
            state, usable = "OK", usable + 1
        elif result.ok:
            state = "raggiungibile"
        else:
            state = "IRRAGGIUNGIBILE"
        print(f"  {name:<21} {state:<13} {len(relevant):>7}  {result.detail[:70]}")

    print(f"\n  {usable} fonti stanno producendo annunci pertinenti.\n")
    print("  Se una fonte è 'raggiungibile' ma con 0 annunci, i selettori CSS in")
    print("  config.yaml non corrispondono più: apri la pagina, ispeziona, aggiorna.")
    print("  Se è 'IRRAGGIUNGIBILE' per anti-bot, usa la ricerca salvata del sito")
    print("  con alert email e lascia fare a email_alerts.\n")
    return 0


def cmd_inspect(args) -> int:
    """Mostra ogni annuncio grezzo e il motivo per cui viene tenuto o scartato.

    È lo strumento da usare quando `probe` dice "raggiungibile, 0 annunci":
    ti fa vedere cosa il parser ha effettivamente letto dalla pagina.
    """
    cfg = Config.load(args.config)
    ctx = Context(cfg, Fetcher(cfg.get("http", {})))

    targets = [s for s in cfg.all_sources
               if (args.source and s.get("name") == args.source)
               or (not args.source and s.get("enabled"))]
    if not targets:
        print(f"Nessuna fonte chiamata '{args.source}'. Nomi disponibili: "
              + ", ".join(s.get("name", "?") for s in cfg.all_sources))
        return 1

    for src_cfg in targets:
        name = src_cfg.get("name", "?")
        try:
            result = sources.build(src_cfg, ctx).collect()
        except Exception as exc:
            print(f"\n=== {name}: eccezione {type(exc).__name__}: {exc}")
            continue

        print(f"\n=== {name} — {len(result.listings)} annunci grezzi — {result.detail}")
        if not result.listings:
            if src_cfg.get("type") == "email":
                print("    Nessun annuncio estratto. Guarda i numeri qui sopra:")
                print("      0 messaggi          -> cartella sbagliata o vuota")
                print("      0 non letti         -> li hai gia aperti tu")
                print("      0 dai mittenti      -> il filtro non li porta qui")
                print("      annunci 0 ma >0 msg -> le email non citano la referenza")
            else:
                print("    Nessun elemento estratto: item_selector non corrisponde,")
                print("    oppure la pagina è renderizzata in JavaScript.")
            continue

        for i, l in enumerate(result.listings, 1):
            extract.enrich(l, src_cfg)
            reason = reject_reason(l, cfg)
            mark = "TENUTO " if reason is None else "scartato"
            price = f"{l.price_eur:,.0f} EUR" if l.price_eur else "prezzo n/d"
            print(f"\n  [{i}] {mark}  {price}")
            print(f"      titolo : {(l.title or '(vuoto)')[:100]}")
            print(f"      url    : {l.url[:100]}")
            print(f"      campi  : ref={l.reference} anno={l.year} "
                  f"cond={l.condition} bracc={l.bracelet} gar={l.warranty_region} "
                  f"fullset={l.full_set} venduto={l.sold}")
            if reason:
                print(f"      MOTIVO : {reason}")
            if args.verbose:
                print(f"      testo  : {(l.raw_text or '')[:400]}")
    print()
    return 0


def cmd_dashboard(args) -> int:
    cfg = Config.load(args.config)
    db = Database(args.db)
    comparables = db.comparables(cfg.references, int(cfg.get("fair_value.lookback_days", 60)))
    engine = FairValueEngine(cfg, comparables)
    path = dash.build(db, engine.summary(), args.dashboard)
    print(f"dashboard → {path}")
    db.close()
    return 0


def cmd_test_notify(args) -> int:
    from .notify.decide import NotifyDecision
    cfg = Config.load(args.config)
    demo = Listing(
        source="test", url="https://example.com/annuncio-di-prova",
        title="Rolex GMT-Master II 126710BLRO Pepsi Jubilee 2025 Full Set",
        raw_text="Unworn, mai lucidato, garanzia italiana 2025, full set",
        price_eur=29800, reference="126710BLRO", year=2025, bracelet="jubilee",
        condition="unworn", full_set=True, never_polished=True,
        warranty_region="IT", seller_trust=5,
    )
    engine = FairValueEngine(cfg, [])
    engine.evaluate(demo)
    Scorer(cfg).score(demo)

    tg = TelegramNotifier()
    if not tg.enabled:
        print("✗ Telegram non configurato "
              "(mancano TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    else:
        ok = tg.send(NotifyDecision(demo, "new", "MESSAGGIO DI PROVA", 50))
        print("✓ Telegram inviato" if ok else "✗ Invio Telegram fallito")

    em = EmailNotifier()
    if not em.enabled:
        print("✗ Email non configurata (mancano SMTP_HOST / SMTP_USER / SMTP_PASS)")
    else:
        ok = em.send_digest([NotifyDecision(demo, "new", "MESSAGGIO DI PROVA", 50)],
                            engine.summary())
        print("✓ Email inviata" if ok else "✗ Invio email fallito")
    return 0


def _i(v):
    return None if v is None else int(v)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="Rolex Radar")
    p.add_argument("command",
                   choices=["check", "probe", "inspect", "dashboard", "test-notify"])
    p.add_argument("source", nargs="?", default=None,
                   help="nome della fonte (solo per inspect)")
    p.add_argument("--config", default=None)
    p.add_argument("--db", default="history.db")
    p.add_argument("--dashboard", default="docs/index.html")
    p.add_argument("--dry-run", action="store_true", help="non scrive nulla, non notifica")
    p.add_argument("--force", action="store_true", help="ignora le ore di silenzio")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="mostra anche il testo grezzo (inspect)")
    args = p.parse_args(argv)

    return {
        "check": cmd_check,
        "probe": cmd_probe,
        "inspect": cmd_inspect,
        "dashboard": cmd_dashboard,
        "test-notify": cmd_test_notify,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
