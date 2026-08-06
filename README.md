# Rolex Radar

Monitoraggio automatico del **Rolex GMT-Master II 126710BLRO “Pepsi”**.
Gira su GitHub Actions quattro volte al giorno, valuta ogni annuncio con un
modello di fair value, e ti scrive su Telegram **solo quando c'è qualcosa da
sapere**.

---

## Prima di partire: due cose che devi sapere

**1. Il 126710BLRO è stato discontinuato ad aprile 2026.**
Rolex lo ha tolto dal catalogo a Watches & Wonders. Il mercato secondario si è
riprezzato verso l'alto: il budget di 24.000 € oggi non intercetta più il
mercato di questa referenza. Nel `config.yaml` il budget è indicativo e **non
viene usato come filtro**: il sistema ragiona sullo scarto rispetto al valore
stimato, non su una soglia fissa. Rivedi i numeri in `budget:` ogni due o tre
mesi guardando la mediana che il sistema stesso calcola.

**2. Chrono24, Watchfinder e WatchCharts non si possono scrapare.**
Usano protezione anti-bot (DataDome / Cloudflare). Aggirarla significa violare
i loro termini d'uso e costruire qualcosa che si rompe ogni due settimane.
Questo progetto prende la strada pulita: **le ricerche salvate con alert
email**. Tu salvi la ricerca sul marketplace, loro ti mandano l'email, il
sistema legge la casella e ne estrae gli annunci. Copertura piena, zero
fragilità. Vedi [§3](#3-alert-email-la-fonte-più-importante).

---

## Come ragiona il sistema

Il pezzo interessante non è la raccolta: è la valutazione.

**Nessun filtro rigido tranne la referenza.** Anno, garanzia, bracciale,
condizione, prezzo: tutto entra nel punteggio, niente esclude. Un 2023
impeccabile a buon prezzo deve arrivarti; un filtro `years: 2024-2026` lo
avrebbe buttato via.

**Fair value edonico.** Confrontare direttamente due annunci è inutile: un 2023
lucidato senza scatola e un 2026 unworn full set non sono lo stesso oggetto.
Quindi ogni annuncio viene *normalizzato* — il prezzo viene diviso per i
moltiplicatori delle sue caratteristiche — e la **mediana** dei prezzi
normalizzati diventa l'indice di mercato. Il fair value di un singolo annuncio
è poi l'indice × i suoi moltiplicatori.

È in piccolo quello che fa un modello edonico, ed è il motivo per cui il sistema
riesce a dirti *«questo 2023 a 28.600 € è un affare migliore di quel 2026 a
39.900 €»* — cosa che nessun marketplace ti dice.

Mediana e non media, perché la mediana è immune agli annunci-civetta e ai
“prezzo su richiesta” mal interpretati. E prima del calcolo viene tagliato il
10% agli estremi.

**Un esempio reale** (output di `python tools/demo.py`):

```
  Indice di mercato stimato: 34.049 €  (10 campioni, data-driven)

   SCORE     PREZZO      STIMA     SCARTO  FONTE / PROFILO
  ─────────────────────────────────────────────────────────────────────────
   86/100 ★★★★☆    33.900€    34.730€      +830€  pedretti  2025 unworn jubilee gar.IT
   82/100 ★★★★☆    30.100€    32.006€    +1.906€  chrono24  2025 unworn jubilee gar.SA
   81/100 ★★★★☆    28.600€    30.340€    +1.740€  pluswatch 2023 mint   jubilee gar.IT
   79/100 ★★★★☆    34.800€    34.389€      −411€  dellarocca 2025 unworn jubilee gar.EU
   ...
   45/100 ★★☆☆☆    25.900€    26.213€      +313€  chrono24  2023 excellent jubilee gar.AE
```

Guarda la seconda riga: garanzia saudita, quindi penalizzata. Ma è 1.900 € sotto
il suo valore stimato, e finisce comunque in cima. Un filtro
`excluded_warranty: Saudi Arabia` te l'avrebbe nascosto. Il quinto invece è
sempre AE ma **non** è sotto mercato, e infatti scende a 45.

---

## Setup

### 0. Requisiti

Un account GitHub. Nient'altro. Il piano gratuito basta: quattro esecuzioni al
giorno da ~2 minuti stanno larghe nei 2.000 minuti/mese inclusi.

### 1. Crea il repository

```bash
cd rolex-radar
git init && git add . && git commit -m "Rolex Radar"
gh repo create rolex-radar --private --source=. --push
```

Se non usi la CLI `gh`: crea un repo **privato** su github.com e fai push.
Privato è importante: il `history.db` racconta cosa stai cercando.

### 2. Bot Telegram (2 minuti)

1. Su Telegram scrivi a **@BotFather** → `/newbot` → scegli un nome.
   Ti restituisce un token tipo `7123456789:AAF...`.
2. Scrivi un messaggio qualsiasi **al tuo bot** (serve a sbloccare la chat).
3. Apri `https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates` e copia
   `result[0].message.chat.id`.

Poi su GitHub: **Settings → Secrets and variables → Actions → New repository
secret**

| Secret | Valore |
|---|---|
| `TELEGRAM_BOT_TOKEN` | il token di BotFather |
| `TELEGRAM_CHAT_ID` | il chat id |

### 3. Alert email: la fonte più importante

Questo è il passaggio che dà accesso a Chrono24 & co.

**a) Crea una casella dedicata.** Non usare la tua principale: il sistema legge
tutta la posta non letta. Un indirizzo Gmail nuovo va benissimo.

**b) Salva le ricerche sui marketplace**, usando quella casella:

- **Chrono24** → cerca `Rolex GMT-Master II 126710BLRO` → *Salva ricerca* →
  notifiche **immediate**. Fai una ricerca **larga**: solo la referenza, senza
  filtri di anno/prezzo/paese. Al filtraggio pensa il sistema.
- **Watchfinder**, **Wristler**, **ChronoHunter**, **Subito** → stessa cosa dove
  disponibile.

**c) Crea una password per le app.** Su Gmail serve la verifica in due passaggi
attiva, poi *Google Account → Sicurezza → Password per le app*. La password
normale non funziona con IMAP.

**d) Aggiungi i secrets:**

| Secret | Valore |
|---|---|
| `IMAP_HOST` | `imap.gmail.com` |
| `IMAP_USER` | l'indirizzo della casella dedicata |
| `IMAP_PASS` | la password per le app |

Il sistema legge solo i messaggi **non letti** dei mittenti elencati in
`config.yaml → sources → email_alerts → from_contains`, e li marca come letti
dopo averli processati.

### 4. Report email (opzionale)

| Secret | Valore |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | l'indirizzo mittente |
| `SMTP_PASS` | password per le app |
| `REPORT_TO` | dove vuoi ricevere il report |

### 5. Verifica che tutto risponda

Su GitHub: **Actions → Rolex Radar → Run workflow**, spuntando *dry run*.
Guarda i log.

Poi, in locale, il comando diagnostico più utile del progetto:

```bash
pip install -r requirements.txt
python -m radar.main probe
```

```
  FONTE                 STATO         ANNUNCI  DETTAGLIO
  ──────────────────────────────────────────────────────────
  email_alerts          OK                  7  7 annunci da email
  pluswatch             OK                  2  ok
  dellarocca            raggiungibile       0  ok
  orologiepassioni      IRRAGGIUNGIBILE     —  HTTP 403 (probabile anti-bot)
```

- **OK** → funziona, lasciala accesa.
- **raggiungibile ma 0 annunci** → i selettori CSS non corrispondono. Apri la
  pagina nel browser, ispeziona la scheda prodotto, aggiorna `item_selector` e
  `fields` in `config.yaml`. Nessun codice da toccare.
- **IRRAGGIUNGIBILE** → metti `enabled: false` e copri quel sito via alert email.

I selettori dei dealer italiani nel `config.yaml` sono **ipotesi ragionevoli**
basate sulle piattaforme più diffuse (WooCommerce, Magento, Shopify): il
`probe` ti dirà quali vanno corretti. È normale doverne sistemare qualcuno.

### 6. Dashboard sul telefono

**Settings → Pages → Source: Deploy from a branch → `main` / `/docs`**.
Dopo un minuto la trovi su `https://<tuo-utente>.github.io/rolex-radar/`.
Aggiungila alla schermata Home: è pensata per il telefono.

> Se il repo è privato, GitHub Pages richiede un piano a pagamento. In
> alternativa apri `docs/index.html` direttamente da GitHub, oppure clona il
> repo in locale.

---

## Uso quotidiano

Nessuno. Gira da solo alle 08, 12, 16 e 20 (ora italiana d'estate; d'inverno
un'ora prima — GitHub Actions ragiona in UTC).

Quello che ricevi:

```
🔥 SOTTO MERCATO · +1.900 €

Rolex GMT-Master II “Pepsi”
126710BLRO

Prezzo      30.100 €
Stima       32.006 €
Scarto      1.906 € sotto (+6,0%)

Anno        2025
Condizioni  unworn
Bracciale   jubilee
Garanzia    SA
Full set    sì
Fonte       chrono24

Score 82/100  ★★★★☆

· 💰 6,0% sotto il valore stimato (+1.906 €)
· anno 2025 — quello che cerchi
· bracciale jubilee
· ⚠︎ garanzia SA — rivendibilità più difficile in Italia

→ Apri l'annuncio
```

### Deduplica

Il problema vero del monitoraggio non è trovare, è **non ripetersi**. Le regole:

| Situazione | Cosa succede |
|---|---|
| Annuncio nuovo, score ≥ soglia | notifica |
| Annuncio nuovo, sotto mercato | notifica **sempre**, anche con score basso |
| Già visto, prezzo sceso ≥ 2% o ≥ 400 € | notifica il calo |
| Già visto, score migliorato di ≥ 6 | notifica |
| Già visto, tutto uguale | **silenzio** |
| Ore 23–07 | passa solo il sotto mercato |
| Più di 12 in un giro | tagliato a 12, i migliori |

Le soglie sono tutte in `config.yaml → notifications`.

L'identità dell'annuncio è l'URL normalizzato (senza `utm_*` e simili), quindi
lo stesso annuncio che ti arriva da due email diverse conta una volta sola.

---

## Comandi

```bash
python -m radar.main check              # giro completo
python -m radar.main check --dry-run    # non scrive nulla, non notifica
python -m radar.main check --force      # ignora le ore di silenzio
python -m radar.main probe              # quali fonti funzionano?
python -m radar.main dashboard          # rigenera solo docs/index.html
python -m radar.main test-notify        # notifica di prova su Telegram + email
python tools/demo.py                    # dati finti, per vedere la dashboard
python -m pytest tests/ -q              # 44 test
```

---

## Struttura

```
config.yaml                 ← il file che tocchi. Il resto quasi mai.
radar/
  models.py                 struttura dell'annuncio, chiave di deduplica
  config.py                 caricamento config
  fetch.py                  HTTP educato: rate limit, retry, robots.txt
  extract.py                ★ il cervello: legge anno, garanzia, corredo...
  sources/
    html_source.py          scraping a 3 livelli: JSON-LD → CSS → euristica
    email_source.py         ★ ingestione degli alert dei marketplace
  fairvalue.py              ★ normalizzazione edonica + mediana
  scorer.py                 punteggio 0-100
  notify/
    decide.py               ★ cosa notificare e cosa no
    telegram.py             messaggi Telegram
    email_report.py         report HTML via SMTP
  dashboard.py              genera docs/index.html
  main.py                   orchestratore + CLI
tools/demo.py               dataset di esempio
tests/                      44 test
history.db                  storico prezzi (versionato di proposito)
docs/index.html             dashboard
```

---

## Personalizzare

### Aggiungere un dealer

Otto righe in `config.yaml`, zero codice:

```yaml
  - name: nuovo_dealer
    type: html
    enabled: true
    country: IT
    dealer: true
    seller_trust: 4
    start_urls:
      - "https://nuovodealer.it/?s=126710BLRO"
    item_selector: "li.product"
    fields:
      title: "h2"
      price: ".price"
      url: "a@href"
      image: "img@src"
```

Poi `python -m radar.main probe` per verificare. Se il sito espone JSON-LD
(molti e-commerce lo fanno), i selettori vengono ignorati e funziona da solo.

### Monitorare un'altra referenza

Cambia `watch.references` e ricalibra `fair_value.seed_price_eur` e i
moltiplicatori `year`. Il resto del sistema è agnostico.

### Ricevere più o meno notifiche

`notifications.min_score` è la manopola principale. 78 è tarato per ricevere
poco; abbassalo a 65 se vuoi vedere più mercato.

---

## Limiti — leggili

- **L'indice di mercato è costruito su prezzi richiesti, non su prezzi di
  transazione.** Il venduto reale è più basso del chiesto, spesso del 3-8%.
  L'indice è un riferimento relativo per confrontare annunci fra loro, non una
  valutazione peritale.
- **Servono ~10 annunci comparabili** perché la stima diventi data-driven. Nei
  primi giorni il sistema usa `seed_price_eur` e la dashboard te lo dice.
- **I moltiplicatori edonici sono tarati a mano.** Sono ragionevoli, non
  misurati. Se dopo qualche settimana l'indice ti sembra sistematicamente
  sbagliato, aggiustali.
- **La conversione valuta usa tassi fissi** in `extract.py`. Per una precisione
  migliore vanno agganciati a un feed.
- **Un annuncio ben scritto vince su uno scritto male**, perché il sistema
  legge solo ciò che è dichiarato. C'è una penalità per gli annunci opachi, ma
  non compensa del tutto.
- **Niente qui verifica l'autenticità dell'orologio.** Uno score 95 dice che
  l'annuncio è interessante, non che l'orologio è autentico o che il venditore
  è onesto. Il seriale, le foto e il venditore vanno sempre controllati a mano.
- **Questo non è consulenza all'acquisto o all'investimento.**

---

## Se qualcosa si rompe

**Nessuna notifica da giorni** → `Actions` verde ma zero annunci? Lancia
`probe`. Molto probabilmente un dealer ha rifatto il sito e i selettori sono
scaduti.

**Il workflow fallisce sul push** → succede se hai committato a mano nel
frattempo. Il workflow fa `git pull --rebase` da solo, ma un conflitto su
`history.db` (file binario) va risolto tenendo la versione remota.

**Troppe notifiche il primo giorno** → normale, il database è vuoto e tutto è
“nuovo”. C'è un tetto di 12 per giro. Dal secondo giorno si calma.

**Le email di alert non vengono lette** → il sistema guarda solo i messaggi
**non letti**. Se il tuo client li ha già aperti, sono persi. Usa una casella
dedicata che non apri mai, o metti `unseen_only: false` (attenzione: rilegge
tutto ogni volta).
