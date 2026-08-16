"""Genera docs/index.html: la dashboard che apri dal telefono.

Una sezione per orologio, richiudibile. Con otto modelli, tenerle tutte aperte
significa scorrere un muro: qui restano aperte solo quelle che hanno annunci,
e ognuna ha in testa una riga che dice tutto senza doverla espandere.

Le foto: se l'orologio ha un campo `photo` in config si usa quello, altrimenti
si prende l'immagine di uno dei suoi annunci — che il sistema già raccoglie.
Se non c'è nessuna delle due, si disegna un segnaposto.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def _eur(v) -> str:
    return f"{v:,.0f} €".replace(",", ".") if v else "—"


def _iniziali(label: str) -> str:
    parole = [p for p in label.replace("“", " ").replace("”", " ").split() if p[:1].isalpha()]
    return "".join(p[0] for p in parole[:2]).upper() or "?"


def _foto(market: dict, listings: list[dict]) -> str:
    """Immagine dell'orologio: dal config, dagli annunci, o segnaposto."""
    url = market.get("photo")
    if not url:
        for l in listings:
            if l.get("image"):
                url = l["image"]
                break
    if url:
        return (f'<img class="ph" src="{html.escape(url)}" alt="" loading="lazy" '
                f'onerror="this.replaceWith(document.createRange()'
                f'.createContextualFragment(this.dataset.fb))" '
                f'data-fb=\'{_segnaposto(market)}\'>')
    return _segnaposto(market)


def _segnaposto(market: dict) -> str:
    return (f'<div class="ph ph-vuota">{html.escape(_iniziali(market.get("label", "?")))}</div>')


_PIANI = {"home": 0, "nearby": 1, "reference": 2}


def _piano(l: dict, market: dict) -> str:
    home = market.get("home") or ["IT", "SM"]
    nearby = market.get("nearby") or ["EU", "CH"]
    c = l.get("seller_country")
    if c in home:
        return "home"
    if c in nearby or c is None:
        return "nearby"
    return "reference"


def build(db, markets, out_path: str | Path = "docs/index.html") -> Path:
    if isinstance(markets, dict):          # retrocompatibilità: un solo orologio
        markets = [markets]
    markets = list(markets) or [{}]

    dati = []
    for m in markets:
        listings = [l for l in db.active_listings(m.get("watch_id")) if l.get("price_eur")]
        # Italia e San Marino in cima: è dove compreresti davvero. Il resto
        # scende, ma resta visibile perché serve a leggere i prezzi.
        listings.sort(key=lambda l: (_PIANI[_piano(l, m)], -(l.get("score") or 0)))
        for l in listings:
            l["_piano"] = _piano(l, m)
        dati.append((m, listings))

    sommario = "\n".join(_riga_sommario(m, ls) for m, ls in dati)
    sezioni = "\n".join(_sezione(db, m, ls) for m, ls in dati)
    totale = sum(len(ls) for _, ls in dati)
    occasioni = sum(1 for _, ls in dati for l in ls if (l.get("delta_pct") or 0) >= 4)

    doc = _TEMPLATE.format(
        updated=datetime.now(ROME).strftime("%d/%m/%Y %H:%M"),
        n_watches=len(markets), totale=totale, occasioni=occasioni,
        sommario=sommario, sezioni=sezioni,
    )
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p


def _gruppo(market: dict) -> str:
    """Etichetta del turno di rotazione, per sapere a colpo d'occhio quando
    un orologio viene controllato senza dover aprire il config."""
    g = market.get("group")
    return f'<span class="tag">{html.escape(str(g))}</span>' if g else ""


def _riga_sommario(market: dict, listings: list[dict]) -> str:
    """Riga compatta in cima: colpo d'occhio su tutti gli orologi."""
    qui = [l for l in listings if l.get("_piano") == "home"]
    under = sum(1 for l in qui if (l.get("delta_pct") or 0) >= 4)
    best = qui[0] if qui else (listings[0] if listings else None)
    if under:
        stato = f'<span class="occ">{under} sotto mercato</span>'
    elif qui:
        stato = f'{len(qui)} in Italia'
    elif listings:
        stato = f'<span class="mut">{len(listings)} altrove</span>'
    else:
        stato = '<span class="mut">nessun annuncio</span>'
    return f"""<a class="srow" href="#{html.escape(str(market.get('watch_id', '')))}">
      {_foto(market, listings)}
      <span class="sname">{html.escape(market.get('label', 'Orologio'))}{_gruppo(market)}</span>
      <span class="sidx">{_eur(market.get('index'))}</span>
      <span class="sstat">{stato}</span>
      <span class="sbest">{best['score'] if best else ''}</span>
    </a>"""


def _sezione(db, market: dict, listings: list[dict]) -> str:
    watch_id = market.get("watch_id") or ""
    label = market.get("label") or "Orologio"
    series = db.market_series(market.get("watch_id"))

    under = [l for l in listings if (l.get("delta_pct") or 0) >= 4]
    best = listings[0] if listings else None
    cheapest = min(listings, key=lambda x: x["price_eur"]) if listings else None

    chart_data = json.dumps([{"t": s["ts"], "median": s["median_eur"]}
                             for s in series if s.get("median_eur")])
    rows = "\n".join(_row(l) for l in listings[:60]) or (
        '<tr><td colspan="3" class="empty">Nessun annuncio attivo.</td></tr>')
    note = "" if market.get("data_driven") else (
        "Stima di partenza: il valore non è ancora calcolato sui dati raccolti.")
    aperta = " open" if listings else ""

    return f"""
    <details class="watch" id="{html.escape(watch_id)}"{aperta}>
      <summary>
        {_foto(market, listings)}
        <span class="wtxt">
          <span class="wname">{html.escape(label)}</span>
          <span class="wsub">{_eur(market.get('index'))} · {len(listings)} annunc{'io' if len(listings) == 1 else 'i'}{' · non aggiornato in questo giro' if market.get('stale') else ''}"""\
f"""{f' · <b class="g">{len(under)} sotto mercato</b>' if under else ''}</span>
        </span>
      </summary>
      <div class="wbody">
        <div class="grid">
          <div class="kpi"><div class="l">Indice</div>
            <div class="v">{_eur(market.get('index'))}</div>
            <div class="s">{market.get('samples', 0)} campion{'e' if market.get('samples') == 1 else 'i'}</div></div>
          <div class="kpi"><div class="l">Più economico</div>
            <div class="v">{_eur(cheapest['price_eur']) if cheapest else '—'}</div>
            <div class="s">fra gli attivi</div></div>
          <div class="kpi"><div class="l">Miglior punteggio</div>
            <div class="v">{best['score'] if best else 0}<span class="pct">/100</span></div>
            <div class="s">a {_eur(best['price_eur']) if best else '—'}</div></div>
        </div>
        {f'<div class="note">{note}</div>' if note else ''}
        <canvas class="chart" data-series='{chart_data}'></canvas>
        <table><tbody>{rows}</tbody></table>
      </div>
    </details>"""


_QUI = None  # segnaposto: le pastiglie di posizione si evidenziano via CSS


def _row(l: dict) -> str:
    e = html.escape
    delta = l.get("delta_pct")
    if delta is None:
        badge = '<span class="b n">n/d</span>'
    elif delta >= 4:
        badge = f'<span class="b g">sotto mercato {delta:+.1f}%</span>'
    elif delta >= -2:
        badge = f'<span class="b y">in linea {delta:+.1f}%</span>'
    else:
        badge = f'<span class="b r">sopra {abs(delta):.1f}%</span>'

    chips = "".join(
        f'<span class="chip{" here" if c == _QUI else ""}">{e(str(c))}</span>'
        for c in [
            (f'📍 {l["seller_country"]}' if l.get("seller_country") else None),
            l.get("year"), (l.get("condition") or "").replace("_", " ") or None,
            l.get("bracelet"),
            f'gar. {l["warranty_region"]}' if l.get("warranty_region") else None,
            "full set" if l.get("full_set") else None,
            "mai lucidato" if l.get("never_polished") else None,
        ] if c
    )
    return f"""<tr class="{l.get('_piano', 'home')}">
      <td class="sc"><div class="ring" style="--v:{l.get('score', 0)}">
        <span>{l.get('score', 0)}</span></div></td>
      <td>
        <div class="pr">{_eur(l.get('price_eur'))}</div>
        <div class="fv">stima {_eur(l.get('fair_value_eur'))} · {badge}</div>
        <div class="chips">{chips}</div>
        <div class="src">{e(l.get('source', ''))}</div>
      </td>
      <td class="go"><a href="{e(l.get('url', '#'))}" target="_blank" rel="noopener">→</a></td>
    </tr>"""


_TEMPLATE = """<!doctype html>
<html lang="it"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1120">
<title>Radar orologi</title>
<style>
  :root {{
    --bg:#0b1120; --card:#131c2e; --line:#233047; --tx:#e8eef8; --mut:#8fa0bb;
    --g:#22c55e; --y:#eab308; --r:#f43f5e; --acc:#38bdf8;
  }}
  * {{ box-sizing:border-box; -webkit-tap-highlight-color:transparent }}
  body {{ margin:0; background:var(--bg); color:var(--tx); font:15px/1.5
    -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    padding:env(safe-area-inset-top) 0 40px }}
  .wrap {{ max-width:760px; margin:0 auto; padding:20px 16px }}
  a {{ color:inherit; text-decoration:none }}
  h1 {{ font-size:20px; margin:2px 0; letter-spacing:-.01em }}
  .eyebrow {{ font-size:11px; letter-spacing:.18em; color:var(--mut) }}
  .upd {{ font-size:12px; color:var(--mut) }}
  .head-k {{ font-size:13px; color:var(--mut); margin:10px 0 18px }}
  .head-k b {{ color:var(--tx) }}

  /* foto / segnaposto */
  .ph {{ width:44px; height:44px; border-radius:12px; object-fit:cover;
    background:#1c2740; border:1px solid var(--line); flex:0 0 44px }}
  .ph-vuota {{ display:grid; place-items:center; font-size:13px; font-weight:700;
    color:#7d90ae; letter-spacing:.04em }}

  /* sommario in cima */
  .sum {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
    overflow:hidden; margin-bottom:22px }}
  .srow {{ display:flex; align-items:center; gap:11px; padding:10px 12px;
    border-bottom:1px solid var(--line) }}
  .srow:last-child {{ border-bottom:0 }}
  .srow:active {{ background:#182338 }}
  .tag {{ display:inline-block; margin-left:6px; padding:1px 7px; border-radius:99px;
          background:#e9ecf2; color:#5a6072; font-size:10px; font-weight:600;
          letter-spacing:.02em; vertical-align:1px; }}
  .sname {{ flex:1; font-size:14px; font-weight:600; letter-spacing:-.01em;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
  .sidx {{ font-size:13px; color:var(--mut); white-space:nowrap }}
  .sstat {{ font-size:11px; color:var(--mut); width:96px; text-align:right;
    white-space:nowrap }}
  .sbest {{ font-size:12px; font-weight:700; color:var(--acc); width:24px;
    text-align:right }}
  .occ {{ color:var(--g); font-weight:600 }}
  .mut {{ color:#5f708c }}

  /* sezioni richiudibili */
  .watch {{ background:var(--card); border:1px solid var(--line);
    border-radius:14px; margin-bottom:12px; overflow:hidden }}
  .watch > summary {{ display:flex; align-items:center; gap:12px; padding:12px;
    cursor:pointer; list-style:none }}
  .watch > summary::-webkit-details-marker {{ display:none }}
  .watch > summary::after {{ content:"⌄"; color:var(--mut); font-size:18px;
    margin-left:auto; transition:transform .15s }}
  .watch[open] > summary::after {{ transform:rotate(180deg) }}
  .wtxt {{ display:flex; flex-direction:column; min-width:0 }}
  .wname {{ font-size:15px; font-weight:650; letter-spacing:-.01em;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
  .wsub {{ font-size:12px; color:var(--mut) }}
  .wbody {{ padding:0 12px 12px }}

  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
    gap:10px; margin-bottom:14px }}
  .kpi {{ background:#182338; border:1px solid var(--line); border-radius:12px;
    padding:12px }}
  .kpi .l {{ font-size:10px; letter-spacing:.1em; color:var(--mut);
    text-transform:uppercase }}
  .kpi .v {{ font-size:20px; font-weight:650; margin-top:3px; letter-spacing:-.02em }}
  .kpi .s {{ font-size:11px; color:var(--mut) }}
  .pct {{ font-size:13px; color:var(--mut) }}
  .note {{ background:#1e293b; border-left:3px solid var(--y); padding:9px 12px;
    border-radius:0 8px 8px 0; font-size:12px; color:var(--mut); margin-bottom:12px }}

  table {{ width:100%; border-collapse:collapse }}
  td {{ padding:12px 6px; border-bottom:1px solid var(--line); vertical-align:top }}
  tr:last-child td {{ border-bottom:0 }}
  .empty {{ color:var(--mut); font-size:13px; text-align:center; padding:18px 0 }}
  .sc {{ width:54px }}
  .ring {{ width:42px; height:42px; border-radius:50%; display:grid; place-items:center;
    background:conic-gradient(var(--acc) calc(var(--v)*1%), #233047 0); font-size:12px;
    font-weight:700 }}
  .ring span {{ width:33px; height:33px; border-radius:50%; background:var(--card);
    display:grid; place-items:center }}
  .pr {{ font-size:16px; font-weight:650; letter-spacing:-.01em }}
  .fv {{ font-size:12px; color:var(--mut); margin-top:2px }}
  .chips {{ margin-top:6px }}
  .chip {{ display:inline-block; background:#1c2740; border:1px solid var(--line);
    color:#b8c6dd; border-radius:99px; padding:2px 9px; font-size:11px;
    margin:0 4px 4px 0 }}
  .chip:first-child {{ background:#14301f; border-color:#1d5236; color:#7ee2a8 }}
  .src {{ font-size:11px; color:#5f708c; margin-top:4px }}
  .b {{ font-weight:600 }} .g {{ color:var(--g) }} .y {{ color:var(--y) }}
  .r {{ color:var(--r) }} .n {{ color:var(--mut) }}
  .go a {{ color:var(--acc); font-size:22px; padding:6px 4px }}
  /* i piani geografici: l'Italia in evidenza, il resto in tono minore */
  tr.nearby {{ opacity:.78 }}
  tr.reference {{ opacity:.5 }}
  tr.reference .chip:first-child, tr.nearby .chip:first-child {{
    background:#1c2740; border-color:var(--line); color:#9fb0c9 }}
  canvas.chart {{ width:100%; height:130px; background:#182338; border-radius:12px;
    border:1px solid var(--line); padding:8px; margin-bottom:12px }}
  footer {{ margin-top:30px; font-size:11px; color:#5f708c; line-height:1.6 }}
</style></head><body>
<div class="wrap">
  <div class="eyebrow">RADAR OROLOGI</div>
  <h1>{n_watches} modelli monitorati</h1>
  <div class="upd">aggiornato {updated}</div>
  <div class="head-k"><b>{totale}</b> annunci attivi · <b>{occasioni}</b> sotto mercato</div>

  <div class="sum">{sommario}</div>

  {sezioni}

  <footer>
    Dati raccolti automaticamente da fonti pubbliche e dagli alert email dei
    marketplace. L'“indice” è una stima statistica costruita sugli annunci
    osservati — prezzi richiesti, non prezzi di transazione. È un indicatore,
    non una valutazione peritale, e non costituisce consulenza all'acquisto o
    all'investimento. Nessun punteggio dice nulla sull'autenticità
    dell'orologio o sull'affidabilità del venditore.
  </footer>
</div>
<script>
  document.querySelectorAll('canvas.chart').forEach(function (c) {{
    function disegna() {{
      if (!c.clientWidth) return;
      var data = JSON.parse(c.dataset.series || '[]');
      var dpr = window.devicePixelRatio || 1;
      c.width = c.clientWidth * dpr; c.height = 130 * dpr;
      var x = c.getContext('2d'); x.scale(dpr, dpr);
      var W = c.clientWidth, H = 130, P = 24;
      if (data.length < 2) {{
        x.fillStyle = '#5f708c'; x.font = '12px -apple-system,sans-serif';
        x.fillText('Servono almeno due rilevazioni.', P, H / 2);
        return;
      }}
      var vals = data.map(function (d) {{ return d.median; }});
      var lo = Math.min.apply(null, vals) * 0.99;
      var hi = Math.max.apply(null, vals) * 1.01;
      var px = function (i) {{ return P + i * (W - 2 * P) / (data.length - 1); }};
      var py = function (v) {{ return H - P - (v - lo) / (hi - lo) * (H - 2 * P); }};
      var grad = x.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, 'rgba(56,189,248,.35)');
      grad.addColorStop(1, 'rgba(56,189,248,0)');
      x.beginPath(); x.moveTo(px(0), py(vals[0]));
      vals.forEach(function (v, i) {{ x.lineTo(px(i), py(v)); }});
      x.lineTo(px(vals.length - 1), H - P); x.lineTo(px(0), H - P); x.closePath();
      x.fillStyle = grad; x.fill();
      x.beginPath(); x.moveTo(px(0), py(vals[0]));
      vals.forEach(function (v, i) {{ x.lineTo(px(i), py(v)); }});
      x.strokeStyle = '#38bdf8'; x.lineWidth = 2; x.stroke();
      x.fillStyle = '#8fa0bb'; x.font = '11px -apple-system,sans-serif';
      x.fillText(Math.round(hi).toLocaleString('it-IT') + ' €', 4, 12);
      x.fillText(Math.round(lo).toLocaleString('it-IT') + ' €', 4, H - 6);
    }}
    disegna();
    // le sezioni chiuse hanno larghezza zero: si ridisegna all'apertura
    var d = c.closest('details');
    if (d) d.addEventListener('toggle', disegna);
  }});
</script>
</body></html>
"""
