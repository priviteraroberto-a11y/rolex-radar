#!/usr/bin/env bash
#
# Porta in produzione gli aggiornamenti, risolvendo da solo il conflitto sul
# database.
#
#   ./tools/pubblica.sh "/percorso/rolex-radar-update" "Messaggio del commit"
#   ./tools/pubblica.sh                                 # solo commit e push
#
# Perche' esiste
# --------------
# `history.db` e' un file binario che cambia da tutte e due le parti: qui
# quando provi il sistema, su GitHub a ogni giro automatico. Git non sa
# fonderlo e si ferma, in mezzo a un'operazione, con un messaggio che sembra
# grave e non lo e'. E' gia' successo abbastanza volte da meritare venti righe
# di script.
#
# La regola: **sul database vince sempre GitHub**. Li' girano quattro
# controlli al giorno, quindi la sua copia e' piu' aggiornata della tua. Il
# codice invece non viene mai risolto in automatico: se c'e' un conflitto sul
# codice lo script si ferma e te lo dice.

set -euo pipefail
cd "$(dirname "$0")/.."

AGGIORNAMENTO="${1:-}"
MESSAGGIO="${2:-Aggiornamenti}"

if [ -n "$AGGIORNAMENTO" ]; then
  echo "→ copio da $AGGIORNAMENTO"
  cp -R "${AGGIORNAMENTO%/}/" .
fi

# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

echo "→ test"
python -m pytest tests/ -q

echo "→ commit"
git add -A
if git diff --cached --quiet; then
  echo "  niente da salvare"
else
  git commit -q -m "$MESSAGGIO"
fi

echo "→ scarico da GitHub"
if ! git pull --no-rebase --quiet; then
  CONFLITTI="$(git diff --name-only --diff-filter=U)"
  if [ "$CONFLITTI" = "history.db" ]; then
    echo "  conflitto sul database: tengo la versione di GitHub, piu' aggiornata"
    git checkout --theirs history.db
    git add history.db
    git commit -q --no-edit
  else
    echo "" >&2
    echo "Conflitto sul CODICE, non sul database:" >&2
    echo "$CONFLITTI" | sed 's/^/  /' >&2
    echo "" >&2
    echo "Questo va guardato a mano. Per annullare tutto:  git merge --abort" >&2
    exit 1
  fi
fi

echo "→ pubblico"
git push --quiet
echo "fatto: $(git log --oneline -1)"
