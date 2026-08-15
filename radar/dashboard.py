"""Genera docs/index.html: la dashboard che apri dal telefono.

Una sezione per orologio monitorato. Ogni sezione ha il suo indice di mercato,
il suo grafico e i suoi annunci: i modelli non vanno mai mescolati, perché una
mediana calcolata su orologi diversi non significa nulla.
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


def build(db, markets, out_path: str | Path = "docs/index.html") -> Path:
    """`markets` è la lista dei riepiloghi, uno per orologio."""
    if isinstance(markets, dict):          # retrocompatibilità: un solo orologio
        markets = [markets]
    markets = list(markets) or [{}]

    sections = "\n".join(_section(db, m, i) for i, m in enumerate(markets))
    updated = datetime.now(ROME).strftime("%d/%m/%Y %H:%M")

    doc = _TEMPLATE.format(updated=updated, sections=sections,
                           n_watches=len(markets))
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p


def _section(db, market: dict, idx: int) -> str:
    watch_id = market.get("watch_id")
    label = market.get("label") or "Orologio"

    listings = [l for l in db.active_listings(watch_id) if l.get("price_eur")]
    series = db.market_series(watch_id)

    under = [l for l in listings if (l.get("delta_pct") or 0) >= 4]
    best = listings[0] if listings else None
    cheapest = min(listings, key=lambda x: x["price_eur"]) if listings else None

    chart_data = json.dumps([
        {"t": s["ts"], "median": s["median_eur"]}
        for s in series if s.get("median_eur")
    ])

    rows = "\n".join(_row(l) for l in listings[:60]) or (
        '<tr><td colspan="3" class="empty">Nessun annuncio attivo al momento.</td></tr>')

    note = "" if market.get("data_driven") else (
        "Stima di partenza: servono più annunci nel database per una "
        "valutazione basata sui dati.")

    return f"""
    <section class="watch">
      <h2 class="wname">{html.escape(label)}</h2>
      <div class="grid">
        <div class="kpi"><div class="l">Indice di mercato</div>
          <div class="v">{_eur(market.get('index'))}</div>
          <div class="s">{market.get('samples', 0)} annunci nel campione</div></div>
        <div class="kpi"><div class="l">Mediana listini</div>
          <div class="v">{_eur(market.get('median_raw'))}</div>
          <div class="s">prezzi non normalizzati</div></div>
        <div class="kpi"><div class="l">Più economico</div>
          <div class="v">{_eur(cheapest['price_eur']) if cheapest else '—'}</div>
          <div class="s">fra gli annunci attivi</div></div>
        <div class="kpi"><div class="l">Sotto mercato</div>
          <div class="v">{len(under)}</div>
          <div class="s">su {len(listings)} attivi</div></div>
        <div class="kpi"><div class="l">Miglior punteggio</div>
          <div class="v">{best['score'] if best else 0}<span class="pct">/100</span></div>
          <div class="s">a {_eur(best['price_eur']) if best else '—'}</div></div>
      </div>
      {f'<div class="note">{note}</div>' if note else ''}
      <canvas class="chart" data-series='{chart_data}'></canvas>
      <table><tbody>{rows}</tbody></table>
    </section>"""


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
        badge = f'<span class="b r">sopra {delta:+.1f}%</span>'

    chips = "".join(
        f'<span class="chip">{e(str(c))}</span>' for c in [
            l.get("year"), (l.get("condition") or "").replace("_", " ") or None,
            l.get("bracelet"),
            f'gar. {l["warranty_region"]}' if l.get("warranty_region") else None,
            "full set" if l.get("full_set") else None,
            "mai lucidato" if l.get("never_polished") else None,
        ] if c
    )

    return f"""<tr>
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
<title>Rolex Radar</title>
<style>
  :root {{
    --bg:#0b1120; --card:#131c2e; --line:#233047; --tx:#e8eef8; --mut:#8fa0bb;
    --g:#22c55e; --y:#eab308; --r:#f43f5e;
  }}
  * {{ box-sizing:border-box; -webkit-tap-highlight-color:transparent }}
  body {{ margin:0; background:var(--bg); color:var(--tx); font:15px/1.5
    -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    padding:env(safe-area-inset-top) 0 40px }}
  .wrap {{ max-width:760px; margin:0 auto; padding:20px 16px }}
  h1 {{ font-size:20px; margin:2px 0; letter-spacing:-.01em }}
  .eyebrow {{ font-size:11px; letter-spacing:.18em; color:var(--mut) }}
  .upd {{ font-size:12px; color:var(--mut); margin-bottom:8px }}
  .watch {{ border-top:1px solid var(--line); margin-top:26px; padding-top:20px }}
  .watch:first-of-type {{ border-top:0; margin-top:14px }}
  .wname {{ font-size:17px; margin:0 0 14px; letter-spacing:-.01em }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:10px; margin-bottom:16px }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:14px }}
  .kpi .l {{ font-size:11px; letter-spacing:.1em; color:var(--mut);
    text-transform:uppercase }}
  .kpi .v {{ font-size:23px; font-weight:650; margin-top:4px; letter-spacing:-.02em }}
  .kpi .s {{ font-size:12px; color:var(--mut) }}
  .pct {{ font-size:14px; color:var(--mut) }}
  .note {{ background:#1e293b; border-left:3px solid var(--y); padding:10px 14px;
    border-radius:0 8px 8px 0; font-size:13px; color:var(--mut); margin-bottom:14px }}
  table {{ width:100%; border-collapse:collapse }}
  td {{ padding:14px 8px; border-bottom:1px solid var(--line); vertical-align:top }}
  .empty {{ color:var(--mut); font-size:13px; text-align:center }}
  .sc {{ width:58px }}
  .ring {{ width:44px; height:44px; border-radius:50%; display:grid; place-items:center;
    background:conic-gradient(#38bdf8 calc(var(--v)*1%), #233047 0); font-size:13px;
    font-weight:700 }}
  .ring span {{ width:34px; height:34px; border-radius:50%; background:var(--card);
    display:grid; place-items:center }}
  .pr {{ font-size:17px; font-weight:650; letter-spacing:-.01em }}
  .fv {{ font-size:12px; color:var(--mut); margin-top:2px }}
  .chips {{ margin-top:7px }}
  .chip {{ display:inline-block; background:#1c2740; border:1px solid var(--line);
    color:#b8c6dd; border-radius:99px; padding:2px 9px; font-size:11px;
    margin:0 4px 4px 0 }}
  .src {{ font-size:11px; color:#5f708c; margin-top:4px }}
  .b {{ font-weight:600 }} .g {{ color:var(--g) }} .y {{ color:var(--y) }}
  .r {{ color:var(--r) }} .n {{ color:var(--mut) }}
  .go a {{ color:#38bdf8; text-decoration:none; font-size:22px; padding:6px 4px }}
  canvas.chart {{ width:100%; height:150px; background:var(--card); border-radius:14px;
    border:1px solid var(--line); padding:8px; margin-bottom:14px }}
  footer {{ margin-top:30px; font-size:11px; color:#5f708c; line-height:1.6 }}
</style></head><body>
<div class="wrap">
  <div class="eyebrow">ROLEX RADAR</div>
  <h1>{n_watches} orologi monitorati</h1>
  <div class="upd">aggiornato {updated}</div>

  {sections}

  <footer>
    Dati raccolti automaticamente da fonti pubbliche e dagli alert email dei
    marketplace. L'“indice di mercato” è una stima statistica costruita sugli
    annunci osservati — prezzi richiesti, non prezzi di transazione. È un
    indicatore, non una valutazione peritale, e non costituisce consulenza
    all'acquisto o all'investimento. Nessun punteggio dice nulla
    sull'autenticità dell'orologio o sull'affidabilità del venditore.
  </footer>
</div>
<script>
  document.querySelectorAll('canvas.chart').forEach(function (c) {{
    var data = JSON.parse(c.dataset.series || '[]');
    var dpr = window.devicePixelRatio || 1;
    c.width = c.clientWidth * dpr; c.height = 150 * dpr;
    var x = c.getContext('2d'); x.scale(dpr, dpr);
    var W = c.clientWidth, H = 150, P = 26;
    if (data.length < 2) {{
      x.fillStyle = '#5f708c'; x.font = '13px -apple-system,sans-serif';
      x.fillText('Servono almeno due rilevazioni per il grafico.', P, H / 2);
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
  }});
</script>
</body></html>
"""
