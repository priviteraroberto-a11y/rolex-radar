"""Estrazione dei campi dal testo libero di un annuncio.

Questo è il modulo che fa la differenza fra un aggregatore di link e uno
strumento che capisce cosa sta guardando. Lavora su titolo + descrizione
in italiano e inglese.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

# --- valute -------------------------------------------------------------------
# Tassi di ripiego, usati solo se non arriva nulla di più preciso.
FX_TO_EUR = {
    "EUR": 1.0, "USD": 0.92, "GBP": 1.17, "CHF": 1.05,
    "AED": 0.25, "JPY": 0.0059, "HKD": 0.118, "SGD": 0.68,
}

CURRENCY_TOKENS = [
    (r"€|eur\b|euro\b", "EUR"),
    (r"\$|usd\b", "USD"),
    (r"£|gbp\b", "GBP"),
    (r"chf\b|fr\.", "CHF"),
    (r"aed\b|dhs\b", "AED"),
    (r"¥|jpy\b", "JPY"),
]

REF_RE = re.compile(r"\b(1267\s?1[09]\s?BLRO|1267\s?10\s?BLNR|1267\s?10)\b", re.I)

_CURRENT_YEAR = datetime.now().year


def norm(text: str) -> str:
    """Normalizza per il matching: minuscolo, senza accenti, spazi compattati."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).lower().strip()


# =============================================================================
# PREZZO
# =============================================================================

_NUM = r"\d{1,3}(?:[.\s,']\d{3})+(?:[.,]\d{1,2})?|\d{4,7}(?:[.,]\d{1,2})?"
_CUR = r"(?:[€$£]|\beur\b|\busd\b|\bgbp\b|\bchf\b|\baed\b)"

# Un numero adiacente a un simbolo di valuta è quasi certamente un prezzo.
_PRICE_WITH_CUR = re.compile(rf"{_CUR}\s*({_NUM})|({_NUM})\s*{_CUR}", re.I)

# Un numero preceduto da "ref"/"art"/"cod", o seguito da un suffisso di lettere,
# è una referenza. Senza questo, "REF. 116234" diventa un prezzo di 116.234 €.
_REF_NUMBER = re.compile(
    rf"(?:\bref\.?|\breferenza\b|\breference\b|\bmodello\b|\bart\.?|\bcod\.?|\bn\.)"
    rf"\s*[:.]?\s*({_NUM})|({_NUM})\s*[A-Z]{{2,6}}\b",
    re.I,
)


def parse_price(text: str) -> tuple[Optional[float], str]:
    """Ritorna (importo, valuta). Gestisce 23.700 € / €23,700 / EUR 23 700.

    Regola: se nel testo c'è anche un solo numero attaccato a un simbolo di
    valuta, si considerano SOLO quelli. È la differenza fra leggere un prezzo
    e leggere un numero di referenza.
    """
    if not text:
        return None, "EUR"
    t = norm(text)

    if re.search(r"(prezzo su richiesta|price on request|poa|su richiesta|contattaci)", t):
        return None, "EUR"

    currency = "EUR"
    for pattern, code in CURRENCY_TOKENS:
        if re.search(pattern, t):
            currency = code
            break

    # numeri che sono referenze, non prezzi
    banned: set[float] = set()
    for m in _REF_NUMBER.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        v = _to_float(raw) if raw else None
        if v is not None:
            banned.add(v)

    def usable(values: list[float]) -> list[float]:
        return [v for v in values
                if 500 <= v <= 5_000_000 and v not in banned
                and not (1900 <= v <= _CURRENT_YEAR + 2 and v == int(v))]

    # 1) numeri adiacenti a una valuta: i più affidabili
    with_cur: list[float] = []
    for m in _PRICE_WITH_CUR.finditer(t):
        v = _to_float(m.group(1) or m.group(2) or "")
        if v is not None:
            with_cur.append(v)
    good = usable(with_cur)
    if good:
        return max(good), currency

    # 2) ripiego: qualunque numero plausibile che non sia una referenza
    loose = [v for v in (_to_float(m.group(0)) for m in re.finditer(_NUM, t))
             if v is not None]
    good = usable(loose)
    if good:
        return max(good), currency

    return None, currency


def _to_float(raw: str) -> Optional[float]:
    s = raw.replace(" ", "").replace("'", "")
    if "," in s and "." in s:
        # l'ultimo separatore è quello decimale
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # virgola decimale solo se seguita da 1-2 cifre finali
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif "." in s:
        s = s if re.search(r"\.\d{1,2}$", s) else s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def to_eur(amount: Optional[float], currency: str) -> Optional[float]:
    if amount is None:
        return None
    return round(amount * FX_TO_EUR.get(currency.upper(), 1.0), 2)


# =============================================================================
# REFERENZA
# =============================================================================

def parse_reference(text: str) -> Optional[str]:
    m = REF_RE.search(text or "")
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)).upper()


_SPLIT_REF = re.compile(r"^(\d{6})([A-Z]{2,6})$")


def matches_reference(listing_ref: Optional[str], text: str, wanted: list[str]) -> bool:
    """Il filtro duro, tollerante su come la referenza è scritta.

    Riconosce tre forme:
      "126710BLRO"            attaccata
      "126710 BLRO"           separata
      "BLRO REF. 126710"      invertita  <- L'Orologiaio di Corte scrive così
    """
    spaced = norm(text).upper()
    hay = re.sub(r"[\s\-_/.]", "", spaced)

    for w in wanted:
        # i punti vanno tolti anche dalla referenza cercata, altrimenti
        # "310.30.42.50.01.002" non trova mai se stessa nel testo normalizzato
        w_clean = re.sub(r"[\s\-_/.]", "", w).upper()
        if listing_ref and re.sub(r"[\s\-_/.]", "", listing_ref).upper() == w_clean:
            return True
        if w_clean in hay:
            return True

        # forma invertita: base e suffisso presenti ma in ordine diverso
        m = _SPLIT_REF.match(w_clean)
        if m:
            base, suffix = m.group(1), m.group(2)
            if base in hay and re.search(rf"(?<![A-Z]){suffix}(?![A-Z])", spaced):
                return True
    return False


# Molti e-commerce infilano referenze popolari nelle descrizioni di prodotti che
# non c'entrano nulla, per posizionarsi su Google. Se ci fidassimo del solo
# testo, un Datejust con "126710BLRO" in fondo alla scheda verrebbe scambiato
# per un Pepsi. Quindi la referenza deve stare nel TITOLO, oppure il titolo deve
# almeno nominare il modello giusto.

# Una referenza Rolex moderna è 6 cifre che iniziano per 1, spesso seguite da
# un suffisso di lettere. Il suffisso NON è un dettaglio: 126710BLRO è il Pepsi,
# 126710BLNR è il Batman. Stesso numero, orologi diversi, prezzi diversi.
_ROLEX_REF_RE = re.compile(r"(?<![0-9A-Z])(1\d{5})\s*-?\s*([A-Z]{2,6})?(?![0-9A-Z])")


_ROLEX_STYLE = re.compile(r"^\d{6}[A-Z]{0,6}$")


def other_reference_in_title(title: str, wanted: list[str]) -> bool:
    """True se il titolo nomina una referenza Rolex diversa da quelle cercate.

    Vale SOLO per la numerazione Rolex (sei cifre più suffisso). Su altri
    marchi il controllo si disattiva: uno Speedmaster vintage "145022" farebbe
    scattare il pattern `1\d{5}` e verrebbe scartato per errore.
    """
    clean = [re.sub(r"[\s\-_/]", "", w).upper() for w in wanted]
    if not clean or not all(_ROLEX_STYLE.match(w) for w in clean):
        return False

    hay = norm(title).upper()
    wanted_full = set(clean)
    wanted_bases = {w[:6] for w in wanted_full}

    for m in _ROLEX_REF_RE.finditer(hay):
        base, suffix = m.group(1), (m.group(2) or "")
        if suffix:
            # referenza completa: il confronto deve includere il suffisso
            if base + suffix not in wanted_full:
                return True
        elif base not in wanted_bases:
            return True
    return False


def is_target_watch(title: str, text: str, wanted: list[str],
                    model_keywords: list[str] | None = None,
                    exclude_keywords: list[str] | None = None) -> bool:
    """Il vero filtro duro, resistente allo spam SEO.

    Ordine dei controlli:
      1. il titolo nomina un modello escluso → non è lui
      2. referenza nel titolo                → è lui, punto
      3. titolo troppo povero per decidere   → ci si affida al corpo
      4. titolo nomina un'altra referenza    → non è lui
      5. referenza nel corpo + modello nel titolo → è lui
    """
    title = title or ""
    model_keywords = model_keywords or []
    nt_early = norm(title)

    # Batman, Sprite, Root Beer: stesso modello, orologio diverso.
    for k in (exclude_keywords or []):
        if norm(k) in nt_early:
            return False

    if matches_reference(None, title, wanted):
        return True

    if not matches_reference(None, text, wanted):
        return False

    nt = norm(title)
    if len(nt) < 12:
        return True

    if other_reference_in_title(title, wanted):
        return False

    return any(norm(k) in nt for k in model_keywords)


# =============================================================================
# DISPONIBILITA
# =============================================================================

_SOLD_RE = re.compile(
    r"prodotto non disponibile|non (piu )?disponibile|\bvenduto\b|\bvendute?\b|"
    r"sold\s*out|out of stock|esaurito|non disponibile al momento|"
    r"articolo non disponibile",
    re.I,
)


def parse_sold(text: str) -> bool:
    """True se l'annuncio è chiaramente già venduto o non acquistabile."""
    return bool(_SOLD_RE.search(norm(text or "")))


# =============================================================================
# ANNO
# =============================================================================

_YEAR_CONTEXT = re.compile(
    r"(?:anno|year|jahr|annee|del|of|dated|produzione|prod\.?|circa|ca\.?|"
    r"garanzia|warranty|card|papers|cartellino|box\s*&?\s*papers|full\s*set)"
    r"[^\d]{0,25}(19[89]\d|20[0-4]\d)"
    r"|(19[89]\d|20[0-4]\d)\s*(?:anno|year|model|modello)",
    re.I,
)


def parse_year(text: str) -> Optional[int]:
    """Preferisce anni con contesto esplicito; altrimenti anni plausibili isolati."""
    if not text:
        return None
    t = norm(text)

    # evita di leggere l'anno dentro numeri lunghi (referenze, seriali)
    t_masked = re.sub(r"\d{5,}", " ", t)

    for m in _YEAR_CONTEXT.finditer(t_masked):
        y = m.group(1) or m.group(2)
        if y:
            yi = int(y)
            if 1980 <= yi <= _CURRENT_YEAR + 1:
                return yi

    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", t_masked)]
    years = [y for y in years if 1980 <= y <= _CURRENT_YEAR + 1]
    if years:
        return max(years)          # in un annuncio l'anno più recente è quasi sempre quello giusto
    return None


# =============================================================================
# BRACCIALE / CONDIZIONE / CORREDO
# =============================================================================

def parse_bracelet(text: str) -> Optional[str]:
    t = norm(text)
    if re.search(r"\bjubil(e|e?e|ee|aum|é)\b|\bgiubileo\b", t):
        return "jubilee"
    if re.search(r"\boyster\s*(bracelet|bracciale)?\b", t) and "oysterflex" not in t:
        # "Oyster Perpetual" non indica il bracciale
        if not re.search(r"oyster\s+perpetual", t):
            return "oyster"
    return None


# L'ordine conta: le espressioni più specifiche vanno prima di quelle generiche,
# altrimenti "come nuovo" verrebbe letto come "new".
_CONDITION_PATTERNS = [
    ("unworn", r"\bunworn\b|mai indossat|nuovo mai indossat|new old stock|\bnos\b|stickered|con adesiv"),
    ("like_new", r"like new|quasi nuovo|near mint"),
    ("mint", r"\bmint\b|pari al nuovo|come nuovo|as new"),
    ("new", r"\bnew\b|\bnuov[oa]\b|\bneu\b|brand new"),
    ("excellent", r"\bexcellent\b|\beccellent|ottime condizioni|ottimo stato"),
    ("very_good", r"very good|molto buon|buone condizioni"),
    ("good", r"\bgood\b|\bbuono\b|discrete condizioni|segni d'uso|worn"),
]


def parse_condition(text: str) -> Optional[str]:
    t = norm(text)
    for label, pattern in _CONDITION_PATTERNS:
        if re.search(pattern, t):
            return label
    return None


def parse_full_set(text: str) -> Optional[bool]:
    t = norm(text)
    if re.search(r"full\s*set|complete\s*set|scatola e (garanzia|documenti)|"
                 r"box\s*(and|&|\+)\s*papers|corredo completo|completo di tutto|"
                 r"con scatola e garanzia|b\s*&\s*p\b", t):
        return True
    if re.search(r"solo orologio|watch only|senza scatola|no box|no papers|"
                 r"senza garanzia|head only|nudo", t):
        return False
    return None


def parse_never_polished(text: str) -> Optional[bool]:
    t = norm(text)
    if re.search(r"mai lucidat|never polished|unpolished|non lucidat|"
                 r"smussi (intatti|integri)|spigoli vivi", t):
        return True
    if re.search(r"\blucidat[oa]\b|\bpolished\b|lucidatura", t):
        return False
    return None


# =============================================================================
# GARANZIA / PAESE
# =============================================================================

_REGION_PATTERNS = [
    ("IT", r"\bitali|\bitaly\b|\bit\b\s*(warranty|garanzia)|garanzia italian"),
    ("AE", r"\buae\b|emirat|dubai|abu dhabi|\bae\b\s*warranty"),
    ("SA", r"saudi|arabia saudita|\bksa\b|riyadh|jeddah"),
    ("QA", r"\bqatar\b|doha"),
    ("KW", r"\bkuwait\b"),
    ("BH", r"\bbahrain\b"),
    ("OM", r"\boman\b|muscat"),
    ("TR", r"\bturkey\b|turchia|\bturkish\b|istanbul"),
    ("HK", r"hong ?kong\b"),
    ("JP", r"\bjapan\b|giappone|\bjapanese\b|tokyo"),
    ("CH", r"\bswitzerland\b|svizzera|\bswiss\b(?!\s*made)|geneva|ginevra|zurich"),
    ("UK", r"\buk\b|united kingdom|\bengland\b|regno unito|london"),
    ("US", r"\busa\b|united states|\bu\.s\.\b|stati uniti"),
    ("DE", r"germania|\bgermany\b|deutschland"),
    ("FR", r"\bfrance\b|francia"),
    ("ES", r"\bspain\b|spagna|espana"),
    ("NL", r"netherlands|olanda|paesi bassi|amsterdam"),
    ("EU", r"\beurop|\beu\b\s*(warranty|garanzia)|\bue\b\s*garanzia"),
]

_EU_MEMBERS = {"IT", "DE", "FR", "ES", "NL", "EU", "AT", "BE", "PT", "IE"}


def parse_warranty_region(text: str) -> Optional[str]:
    """Cerca prima nel contesto 'garanzia/warranty', poi in tutto il testo."""
    t = norm(text)

    window = None
    m = re.search(r"(garanzia|warranty|card|certificato)(.{0,80})", t, re.S)
    if m:
        window = m.group(2)

    for haystack in (window, t):
        if not haystack:
            continue
        for code, pattern in _REGION_PATTERNS:
            if re.search(pattern, haystack):
                return code
    return None


def normalize_region_group(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    return "EU" if code in _EU_MEMBERS and code != "IT" else code


# =============================================================================
# SERIALE
# =============================================================================

def parse_serial(text: str) -> Optional[str]:
    """Seriali Rolex moderni: 8 caratteri alfanumerici, spesso mascherati."""
    m = re.search(r"\b(?:serial|seriale|s/n|sn)[^\w]{0,6}([A-Z0-9]{4,8}[X*]{0,4})\b",
                  text or "", re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([0-9A-Z]{2}[0-9A-Z]{6})\b(?=.{0,30}(serial|seriale))", text or "", re.I)
    return m.group(1).upper() if m else None


# =============================================================================
# ORCHESTRAZIONE
# =============================================================================

def enrich(listing, source_cfg: dict | None = None):
    """Popola tutti i campi derivabili dal testo dell'annuncio."""
    text = f"{listing.title}\n{listing.raw_text}"
    source_cfg = source_cfg or {}

    if listing.price_eur is None:
        # il campo prezzo dedicato è molto più affidabile del testo intero
        amount, currency = parse_price(listing.raw_price or "")
        if amount is None:
            amount, currency = parse_price(text)
        listing.price_original = amount
        listing.currency = currency
        listing.price_eur = to_eur(amount, currency)

    listing.reference = listing.reference or parse_reference(text)
    listing.year = listing.year or parse_year(text)
    listing.serial = listing.serial or parse_serial(text)
    listing.bracelet = listing.bracelet or parse_bracelet(text)
    listing.condition = listing.condition or parse_condition(text)
    if listing.full_set is None:
        listing.full_set = parse_full_set(text)
    if listing.never_polished is None:
        listing.never_polished = parse_never_polished(text)
    if listing.warranty_region is None:
        listing.warranty_region = normalize_region_group(parse_warranty_region(text))
    if listing.sold is None:
        listing.sold = parse_sold(text)

    if listing.seller_trust == 0:
        listing.seller_trust = int(source_cfg.get("seller_trust", 0))
    if listing.is_dealer is None:
        listing.is_dealer = source_cfg.get("dealer")
    if listing.seller_country is None:
        listing.seller_country = source_cfg.get("country")

    return listing
