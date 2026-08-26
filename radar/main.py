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
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from . import dashboard as dash
from . import metodo
from . import extract, sources
from .config import Config, WatchView
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

def expand_urls(src_cfg: dict, watch: WatchView) -> dict:
    """Costruisce gli URL di ricerca di questa fonte per questo orologio.

    Il segnaposto è `{q}`: viene sostituito con ogni termine di ricerca
    dell'orologio (`search_terms`, o le referenze se non specificati).

    Serve perché le referenze non sono tutte uguali: `126710BLRO` si cerca
    bene per intero, ma `310.30.42.50.01.002` di uno Speedmaster va cercato
    come "Speedmaster Moonwatch" — nessun motore di ricerca di un negozio
    trova una stringa con sei gruppi di cifre puntate.
    """
    terms = watch.watch.get("search_terms") or watch.references
    urls: list[str] = []
    for tpl in src_cfg.get("start_urls", []):
        if "{" not in tpl:
            urls.append(tpl)
            continue
        for term in terms:
            try:
                urls.append(tpl.format(q=quote(str(term)), ref=term,
                                       ref6=str(term)[:6]))
            except (KeyError, IndexError):
                log.warning("segnaposto sconosciuto in %s", tpl)

    extra = (watch.watch.get("extra_urls") or {}).get(src_cfg.get("name"), [])
    urls.extend(extra)
    return {**src_cfg, "start_urls": list(dict.fromkeys(urls))}


def collect(cfg: Config, ctx: Context,
            watch: WatchView | None = None) -> tuple[list[Listing], list[str]]:
    """Ritorna (annunci grezzi, nomi delle fonti che hanno risposto)."""
    all_listings: list[Listing] = []
    # Solo le fonti che hanno restituito ALMENO UN annuncio. Una ricerca che
    # torna vuota non prova che il negozio abbia svuotato la vetrina: prova
    # solo che quella query non ha trovato nulla — e le query cambiano, si
    # rompono, cambiano indicizzazione. Chiudere gli annunci su quella base
    # cancella ritrovamenti veri.
    produttive: list[str] = []

    for src_cfg in cfg.sources:
        if watch is not None:
            src_cfg = expand_urls(src_cfg, watch)
        name = src_cfg.get("name", "?")
        try:
            source = sources.build(src_cfg, ctx)
            result = source.collect()
        except Exception as exc:
            log.error("[%s] eccezione: %s: %s", name, type(exc).__name__, exc)
            continue

        if result.ok:
            if result.listings:
                produttive.append(name)
            log.info("[%s] %d annunci — %s", name, len(result.listings), result.detail)
        else:
            log.warning("[%s] FONTE NON RAGGIUNGIBILE — %s", name, result.detail)

        for l in result.listings:
            extract.enrich(l, src_cfg)
        all_listings.extend(result.listings)

    return all_listings, produttive


def reject_reason(l: Listing, cfg) -> Optional[str]:
    """Perché questo annuncio non ci interessa? None = va tenuto.

    Tenere la logica di scarto in un posto solo serve a due cose: evitare che
    `check` e `inspect` divergano, e poter spiegare all'utente cosa è successo.
    """
    wanted = cfg.references
    keywords = getattr(cfg, "model_keywords", None) or cfg.get("watch.model_keywords", [])
    excluded = getattr(cfg, "exclude_keywords", None) or cfg.get("watch.exclude_keywords", [])
    hf = cfg.get("hard_filters", {})
    lo = float(hf.get("absolute_min_price_eur", 0))
    hi = float(hf.get("absolute_max_price_eur", 10**9))
    ymin, ymax = int(hf.get("min_year", 1900)), int(hf.get("max_year", 2100))

    text = f"{l.title} {l.raw_text}"

    # Il controllo sull'indirizzo sta qui, non solo dentro le fonti: cosi'
    # vale per qualunque origine e `inspect` lo spiega come tutti gli altri.
    if not extract.e_url_di_annuncio(l.url):
        return "non e' la pagina di un annuncio (vetrina, categoria o immagine)"

    altra = extract.altra_marca_nel_titolo(l.title, getattr(cfg, "brand", None))
    if altra:
        return f"il titolo parla di {altra}, non di {cfg.brand}"

    variante = extract.referenza_esclusa(
        l.title, l.raw_text, getattr(cfg, "references_esatte", []) or [],
        getattr(cfg, "exclude_references", []) or [])
    if variante:
        return f"variante esclusa: {variante}"

    if getattr(cfg, "identify_by", "reference") == "name":
        if not extract.matches_by_name(l.title, text, cfg.brand,
                                       cfg.must_include, excluded):
            low = extract.norm(l.title)
            for k in excluded:
                if extract.norm(k) in low:
                    return f"modello escluso: {k}"
            mancanti = [k for k in cfg.must_include
                        if extract.norm(k) not in extract.norm(f"{l.title} {text}")]
            return f"nome incompleto, manca: {', '.join(mancanti) or 'la marca'}"
    elif not extract.matches_reference(l.reference, text, wanted):
        return "referenza assente dal testo"
    elif not extract.is_target_watch(l.title, text, wanted, keywords, excluded):
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


def filter_relevant(listings: list[Listing], cfg) -> list[Listing]:
    """L'unico filtro duro: la referenza. Più i limiti di sanità sul prezzo."""
    # Lo stesso annuncio può arrivare da due pagine della stessa fonte (una
    # categoria e una ricerca) con dettagli diversi. Non basta scartare il
    # secondo: va tenuto quello che ha piu informazioni.
    best: dict[str, Listing] = {}
    for l in listings:
        reason = reject_reason(l, cfg)
        if reason:
            log.debug("scartato (%s): %s", reason, l.url)
            continue
        prev = best.get(l.key)
        if prev is None or _richness(l) > _richness(prev):
            best[l.key] = l
    return list(best.values())


def _richness(l: Listing) -> int:
    """Quante informazioni utili porta questo annuncio."""
    score = 3 if l.price_eur is not None else 0
    for v in (l.year, l.condition, l.bracelet, l.full_set,
              l.warranty_region, l.never_polished, l.image):
        if v is not None:
            score += 1
    return score


# =============================================================================
# comandi
# =============================================================================

def check_one_watch(watch, cfg: Config, ctx: Context, db: Database,
                    args) -> tuple[dict, list]:
    """Un giro completo per UN orologio. Ritorna (mercato, decisioni)."""
    log.info("")
    log.info("═══ %s  %s", watch.label, ", ".join(watch.references))
    log.info("── raccolta ──────────────────────────────────────────")

    raw, produttive = collect(cfg, ctx, watch)
    listings = filter_relevant(raw, watch)
    log.info("%d annunci grezzi → %d pertinenti", len(raw), len(listings))

    # Il fair value di questo orologio guarda solo le SUE referenze: mescolare
    # un Daytona con un GMT produrrebbe una mediana priva di significato.
    lookback = int(watch.get("fair_value.lookback_days", 60))
    comparables = db.comparables(watch.references, lookback)
    comparables += [
        {"price_eur": l.price_eur, "year": l.year, "condition": l.condition,
         "bracelet": l.bracelet, "full_set": _i(l.full_set),
         "warranty_region": l.warranty_region, "never_polished": _i(l.never_polished)}
        for l in listings if l.price_eur
    ]
    engine = FairValueEngine(watch, comparables)
    market = engine.summary()
    market["label"] = watch.label
    market["group"] = watch.watch.get("group")
    market["watch_id"] = watch.id
    market["photo"] = watch.watch.get("photo")
    geo = watch.get("preferences.geography", {}) or {}
    market["home"], market["nearby"] = geo.get("home"), geo.get("nearby")
    log.info("indice %s € su %d campioni (%s)", f"{market['index']:,.0f}",
             market["samples"],
             "data-driven" if market["data_driven"] else "stima di partenza")

    scorer = Scorer(watch)
    scored: list[tuple[Listing, dict]] = []
    for l in listings:
        engine.evaluate(l)
        scorer.score(l)
        change = (db.upsert(l, watch.id) if not args.dry_run
                  else {"is_new": True, "old_price": None, "old_score": 0})
        scored.append((l, change))

    scored.sort(key=lambda t: -t[0].score)
    for l, _ in scored[:10]:
        log.info("  %3d/100  %9s  %s  %s", l.score,
                 f"{l.price_eur:,.0f}€" if l.price_eur else "n/d",
                 f"{l.year or '????'}", l.url[:70])

    if not args.dry_run:
        closed = db.mark_inactive_except([l.key for l in listings], produttive, watch.id)
        if closed:
            log.info("%d annunci non più online", closed)
        db.save_market_snapshot(
            watch.references[0] if watch.references else "?",
            market["samples"], market["median_raw"], market["p25"],
            market["index"], watch.id,
        )
        for name in produttive:
            db.log_run(name, True, len(listings))

    decisions = decide_notifications(scored, watch, engine.is_underpriced,
                                     force=args.force)
    log.info("%d notifiche da inviare", len(decisions))
    return market, decisions


def select_watches(cfg: Config, args) -> tuple[list, str]:
    """Sceglie quali orologi controllare in questo giro.

    Con nove orologi e dieci fonti un giro solo diventa lungo. La rotazione
    divide gli orologi in gruppi e ne controlla uno per volta, alternandoli:
    ogni orologio viene guardato ogni due giri, cioè comunque due volte al
    giorno. Gli orologi senza gruppo (o con `group: always`) restano in ogni
    giro — è dove metti quelli che non vuoi perdere di vista.
    """
    watches = cfg.watches
    if getattr(args, "all_watches", False) or not cfg.get("rotation.enabled", False):
        return watches, "tutti"

    groups = [str(g) for g in (cfg.get("rotation.groups") or [])]
    if not groups:
        ordered = [w.watch.get("group") for w in watches if w.watch.get("group")]
        groups = sorted(set(ordered) - {"always"})
    if not groups:
        return watches, "tutti"

    if getattr(args, "group", None):
        # Gli alias servono a non rompere niente quando i gruppi vengono
        # rinominati: un lancio schedulato o un segnalibro con il nome vecchio
        # continua a funzionare invece di selezionare zero orologi in silenzio.
        alias = {str(k): str(v) for k, v in (cfg.get("rotation.aliases") or {}).items()}
        chosen = alias.get(str(args.group), str(args.group))
        # Un singolo orologio, per id: utile quando ne aggiungi uno e vuoi
        # vedere subito se le fonti lo trovano, senza aspettare il suo turno.
        solo = [w for w in watches if w.id == chosen]
        if solo:
            return solo, chosen
        if chosen not in groups:
            # Meglio un giro completo che un giro quasi vuoto: un nome
            # sbagliato non deve tradursi in "non ho guardato" senza dirlo.
            log.warning("'%s' non e' ne' un gruppo (%s) ne' un orologio — controllo tutti",
                        args.group, ", ".join(groups))
            return watches, "tutti"
    else:
        # fascia di 4 ore: i giri delle 8/12/16/20 si alternano da soli
        slot = int(datetime.now(timezone.utc).timestamp() // (4 * 3600))
        chosen = groups[slot % len(groups)]

    picked = [w for w in watches
              if w.watch.get("group") in (chosen, "always", None)]
    return picked, chosen


def cmd_check(args) -> int:
    cfg = Config.load(args.config)
    ctx = Context(cfg, Fetcher(cfg.get("http", {})))
    db = Database(args.db)

    if not cfg.watches:
        log.error("Nessun orologio configurato: manca la sezione `watches:`")
        return 1
    chiusi = db.close_unmonitored({w.id for w in cfg.watches})
    if chiusi:
        log.info("%d annunci di orologi non piu' monitorati messi a riposo", chiusi)

    watches, gruppo = select_watches(cfg, args)
    if not watches:
        log.warning("Il gruppo '%s' non contiene orologi: niente da fare", gruppo)
        return 0
    log.info("Gruppo di turno: %s  (%d di %d orologi)",
             gruppo, len(watches), len(cfg.watches))
    log.info("In questo giro: %s", ", ".join(w.label for w in watches))

    markets: list[dict] = []
    all_decisions: list = []
    for watch in watches:
        try:
            market, decisions = check_one_watch(watch, cfg, ctx, db, args)
        except Exception as exc:
            # Un orologio che esplode non deve fermare gli altri.
            log.error("[%s] giro fallito: %s: %s", watch.id, type(exc).__name__, exc)
            continue
        markets.append(market)
        all_decisions.extend(decisions)

    # La dashboard deve mostrare SEMPRE tutti gli orologi, non solo quelli del
    # gruppo di turno: altrimenti con la rotazione meta' dei modelli sparisce
    # dalla pagina a ogni giro, insieme agli annunci che avevano gia' trovato.
    controllati = {m["watch_id"] for m in markets}
    for watch in cfg.watches:
        if watch.id in controllati:
            continue
        comps = db.comparables(watch.references,
                               int(watch.get("fair_value.lookback_days", 60)))
        m = FairValueEngine(watch, comps).summary()
        m["label"], m["watch_id"] = watch.label, watch.id
        m["group"] = watch.watch.get("group")
        m["photo"] = watch.watch.get("photo")
        geo = watch.get("preferences.geography", {}) or {}
        m["home"], m["nearby"] = geo.get("home"), geo.get("nearby")
        m["stale"] = True          # non controllato in questo giro
        markets.append(m)
    ordine = {w.id: i for i, w in enumerate(cfg.watches)}
    markets.sort(key=lambda m: ordine.get(m["watch_id"], 999))

    log.info("")
    log.info("── rete ──────────────────────────────────────────────")
    log.info("%s", ctx.fetcher.stats())
    log.info("── notifiche ─────────────────────────────────────────")
    log.info("%d notifiche in totale", len(all_decisions))

    if args.dry_run:
        for d in all_decisions:
            log.info("  [DRY] %-12s %3d/100  %s", d.reason, d.listing.score, d.headline)
    else:
        try:
            tg = TelegramNotifier()
            for d in all_decisions:
                if tg.send(d):
                    db.log_notification(d.listing.key, d.reason, d.listing.price_eur,
                                        d.listing.score)
        except Exception as exc:
            log.error("invio Telegram fallito: %s: %s", type(exc).__name__, exc)
        try:
            if all_decisions:
                EmailNotifier().send_digest(all_decisions, markets[0] if markets else {})
        except Exception as exc:
            log.error("invio email fallito: %s: %s", type(exc).__name__, exc)

    path = dash.build(db, markets, args.dashboard)
    metodo.build(cfg, Path(args.dashboard).with_name("metodo.html"))
    log.info("dashboard → %s", path)
    db.close()
    return 0


def cmd_probe(args) -> int:
    """Diagnostica: dice quali fonti sono realmente utilizzabili."""
    cfg = Config.load(args.config)
    ctx = Context(cfg, Fetcher(cfg.get("http", {})))

    watches = cfg.watches
    print("\n  FONTE                 STATO         ANNUNCI  DETTAGLIO")
    print("  " + "─" * 106)
    usable = 0
    for src_cfg in cfg.all_sources:
        name = src_cfg.get("name", "?")
        if not src_cfg.get("enabled"):
            print(f"  {name:<21} {'spenta':<13} {'—':>7}  enabled: false")
            continue
        relevant, result = [], None
        for w in watches:
            try:
                result = sources.build(expand_urls(src_cfg, w), ctx).collect()
            except Exception as exc:
                print(f"  {name:<21} {'ERRORE':<13} {'—':>7}  {type(exc).__name__}: {exc}")
                result = None
                break
            relevant += filter_relevant(
                [extract.enrich(l, src_cfg) for l in result.listings], w)
        if result is None:
            continue
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
        watch = cfg.watches[0] if cfg.watches else None
        if args.watch:
            match = [w for w in cfg.watches if w.id == args.watch]
            watch = match[0] if match else watch
        try:
            expanded = expand_urls(src_cfg, watch) if watch else src_cfg
            result = sources.build(expanded, ctx).collect()
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
            reason = reject_reason(l, watch or cfg)
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
    markets = []
    for w in cfg.watches:
        comps = db.comparables(w.references, int(w.get("fair_value.lookback_days", 60)))
        m = FairValueEngine(w, comps).summary()
        m["label"], m["watch_id"] = w.label, w.id
        m["group"] = w.watch.get("group")
        m["photo"] = w.watch.get("photo")
        geo = w.get("preferences.geography", {}) or {}
        m["home"], m["nearby"] = geo.get("home"), geo.get("nearby")
        markets.append(m)
    path = dash.build(db, markets, args.dashboard)
    metodo.build(cfg, Path(args.dashboard).with_name("metodo.html"))
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
    p.add_argument("--group", default=None,
                   help="forza un gruppo della rotazione (es. a)")
    p.add_argument("--all-watches", action="store_true",
                   help="ignora la rotazione e controlla tutti gli orologi")
    p.add_argument("--watch", default=None,
                   help="id dell'orologio (solo per inspect)")
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
