# Tarlazzi Bot - Fantaciclismo

Bot Telegram per gestire puntate di fantaciclismo.

## Installazione

Richiede Python 3.10 o superiore.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Configurazione

Imposta il token fornito da BotFather nell'ambiente del processo:

```bash
export TELEGRAM_BOT_TOKEN='token-del-bot'
```

Il token non deve essere salvato nella repository.

## Avvio

Dalla directory della repository, con l'ambiente virtuale attivo:

```bash
python bot.py
```

Il bot usa il polling Telegram e deve restare in esecuzione. Al primo avvio crea automaticamente `tarlazzi.db`; questo file contiene lo stato locale delle gare e deve essere conservato tra i riavvii. `users.json` viene usato come archivio iniziale degli utenti per i solleciti.

Per un servizio persistente su `fps.ms`, configura il processo per:

- eseguire `python bot.py` dalla directory della repository;
- fornire `TELEGRAM_BOT_TOKEN` come variabile d'ambiente o secret;
- usare un volume persistente per `tarlazzi.db`;
- riavviarsi automaticamente se il processo termina.
