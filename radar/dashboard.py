"""Genera docs/index.html: la dashboard che apri dal telefono."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def _eur(v) -> str:
    return f"{v:,.0f} €".replace(",", ".") if v else "—"


def build(db, market: dict, out_path: str | Path = "docs/index.html") -> Path:
    listings = db.active_listings()
    listings = [l for l in listings if l.get("price_eur")]
    series = db.market_series()

    under = [l for l in listings if (l.get("delta_pct") or 0) >= 4]
    best = listings[0] if listings else None
    cheapest = min(listings, key=lambda x: x["price_eur"]) if listings else None

    chart_data = json.dumps([
        {"t": s["ts"], "median": s["median_eur"], "index": s["index_value"]}
        for s in series if s.get("median_eur")
    ])

    rows = "\n".join(_row(l) for l in listings[:80])

    updated = datetime.now(ROME).strftime("%d/%m/%Y %H:%M")

    doc = _TEMPLATE.format(
        updated=updated,
        index=_eur(market.get("index")),
        samples=market.get("samples", 0),
        median=_eur(market.get("median_raw")),
        cheapest=_eur(cheapest["price_eur"]) if cheapest else "—",
        n_active=len(listings),
        n_under=len(under),
        best_score=best["score"] if best else 0,
        best_price=_eur(best["price_eur"]) if best else "—",
        rows=rows,
        chart_data=chart_data,
        note="" if market.get("data_driven") else
             "Stima di partenza: servono più annunci nel database per una "
             "valutazione data-driven.",
    )

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p


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
<title>Rolex Radar · Pepsi 126710BLRO</title>
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
  h1 {{ font-size:20px; margin:2px 0 2px; letter-spacing:-.01em }}
  .eyebrow {{ font-size:11px; letter-spacing:.18em; color:var(--mut) }}
  .upd {{ font-size:12px; color:var(--mut); margin-bottom:18px }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:10px; margin-bottom:22px }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:14px }}
  .kpi .l {{ font-size:11px; letter-spacing:.1em; color:var(--mut);
    text-transform:uppercase }}
  .kpi .v {{ font-size:23px; font-weight:650; margin-top:4px; letter-spacing:-.02em }}
  .kpi .s {{ font-size:12px; color:var(--mut) }}
  .note {{ background:#1e293b; border-left:3px solid var(--y); padding:10px 14px;
    border-radius:0 8px 8px 0; font-size:13px; color:var(--mut); margin-bottom:18px }}
  h2 {{ font-size:13px; letter-spacing:.12em; text-transform:uppercase;
    color:var(--mut); margin:26px 0 10px; font-weight:600 }}
  table {{ width:100%; border-collapse:collapse }}
  td {{ padding:14px 8px; border-bottom:1px solid var(--line); vertical-align:top }}
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
  canvas {{ width:100%; height:180px; background:var(--card); border-radius:14px;
    border:1px solid var(--line); padding:8px }}
  footer {{ margin-top:30px; font-size:11px; color:#5f708c; line-height:1.6 }}
</style></head><body>
<div class="wrap">
  <div class="eyebrow">ROLEX RADAR</div>
  <h1>GMT-Master II “Pepsi” · 126710BLRO</h1>
  <div class="upd">aggiornato {updated}</div>

  <div class="grid">
    <div class="kpi"><div class="l">Indice di mercato</div>
      <div class="v">{index}</div><div class="s">{samples} annunci nel campione</div></div>
    <div class="kpi"><div class="l">Mediana listini</div>
      <div class="v">{median}</div><div class="s">prezzi non normalizzati</div></div>
    <div class="kpi"><div class="l">Più economico</div>
      <div class="v">{cheapest}</div><div class="s">fra gli annunci attivi</div></div>
    <div class="kpi"><div class="l">Sotto mercato</div>
      <div class="v">{n_under}</div><div class="s">su {n_active} attivi</div></div>
    <div class="kpi"><div class="l">Miglior punteggio</div>
      <div class="v">{best_score}<span style="font-size:14px;color:var(--mut)">/100</span></div>
      <div class="s">a {best_price}</div></div>
  </div>

  <div class="note" id="note">{note}</div>

  <h2>Andamento del mercato</h2>
  <canvas id="chart"></canvas>

  <h2>Annunci attivi</h2>
  <table><tbody>{rows}</tbody></table>

  <footer>
    Dati raccolti automaticamente da fonti pubbliche e dagli alert email dei
    marketplace. L'“indice di mercato” è una stima statistica costruita sugli
    annunci osservati (prezzi richiesti, non prezzi di transazione): è un
    indicatore, non una valutazione peritale, e non costituisce consulenza
    all'acquisto o all'investimento.
  </footer>
</div>
<script>
  if (!document.getElementById('note').textContent.trim())
    document.getElementById('note').style.display = 'none';

  const data = {chart_data};
  const c = document.getElementById('chart');
  const dpr = window.devicePixelRatio || 1;
  c.width = c.clientWidth * dpr; c.height = 180 * dpr;
  const x = c.getContext('2d'); x.scale(dpr, dpr);
  const W = c.clientWidth, H = 180, P = 26;
  if (data.length < 2) {{
    x.fillStyle = '#5f708c'; x.font = '13px -apple-system,sans-serif';
    x.fillText('Servono almeno due rilevazioni per il grafico.', P, H / 2);
  }} else {{
    const vals = data.map(d => d.median);
    const lo = Math.min(...vals) * 0.99, hi = Math.max(...vals) * 1.01;
    const px = i => P + i * (W - 2 * P) / (data.length - 1);
    const py = v => H - P - (v - lo) / (hi - lo) * (H - 2 * P);
    const grad = x.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(56,189,248,.35)');
    grad.addColorStop(1, 'rgba(56,189,248,0)');
    x.beginPath(); x.moveTo(px(0), py(vals[0]));
    vals.forEach((v, i) => x.lineTo(px(i), py(v)));
    x.lineTo(px(vals.length - 1), H - P); x.lineTo(px(0), H - P); x.closePath();
    x.fillStyle = grad; x.fill();
    x.beginPath(); x.moveTo(px(0), py(vals[0]));
    vals.forEach((v, i) => x.lineTo(px(i), py(v)));
    x.strokeStyle = '#38bdf8'; x.lineWidth = 2; x.stroke();
    x.fillStyle = '#8fa0bb'; x.font = '11px -apple-system,sans-serif';
    x.fillText(Math.round(hi).toLocaleString('it-IT') + ' €', 4, 12);
    x.fillText(Math.round(lo).toLocaleString('it-IT') + ' €', 4, H - 6);
  }}
</script>
</body></html>
"""
