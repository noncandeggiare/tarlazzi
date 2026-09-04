import logging
import os
import re
from html import escape
from datetime import datetime, timedelta
from collections import Counter
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.constants import ParseMode
from apscheduler.schedulers.background import BackgroundScheduler
from database import Database

# Configurazione logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Stati per conversation handler
DESCRIZIONE, DATA, ORA, CICLISTI, SELEZIONA_GARA_CONTEGGIO, SELEZIONA_GARA_SOLLECITA = range(6)
CONFERMA_ELIMINA = 10

# === CONFIGURAZIONE ===
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise RuntimeError("Imposta la variabile d'ambiente TELEGRAM_BOT_TOKEN")
db = Database()
messaggi_effimeri = {}

# === FUNZIONI HELPER ===
def nome_utente(update: Update) -> str:
    user = getattr(update, 'effective_user', None) or getattr(update, 'from_user', None)
    return f"@{user.username}" if user and user.username else (user.first_name if user else "utente")


def registra_richiesta(update: Update, comando: str):
    logger.info("%s ha fatto /%s nella chat %s", nome_utente(update), comando, update.effective_chat.id)


def get_chat_id_effettivo(update: Update) -> int:
    """Restituisce il gruppo in cui è stato usato il comando."""
    return update.effective_chat.id


def formatta_scadenza(scadenza) -> str:
    """Formatta una scadenza nelle liste come giorno-mese e ora."""
    return datetime.fromisoformat(str(scadenza)).strftime('%d-%m %H:%M')


def genera_lista_gare(gare, callback_prefix: str) -> InlineKeyboardMarkup:
    """Genera la tastiera comune per la selezione di una gara."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{descrizione} {formatta_scadenza(scadenza)}",
            callback_data=f"{callback_prefix}_{gara_id}"
        )]
        for gara_id, descrizione, scadenza in gare
    ])


async def invia_messaggio_effimero(bot, chat_id: int, user_id: int, text: str,
                                   reply_markup=None, callback_query_id=None,
                                   parse_mode=None):
    """Invia un messaggio visibile solo all'utente nel contesto del gruppo."""
    chiave = (chat_id, user_id)
    await elimina_messaggi_effimeri(bot, chat_id, user_id)

    dati = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": reply_markup.to_dict() if reply_markup else None,
        "parse_mode": parse_mode,
        "ephemeral_message_parameters": {
            key: value for key, value in {
                "receiver_user_id": user_id,
                "callback_query_id": callback_query_id,
            }.items() if value is not None
        },
    }
    risposta = await bot._post("sendMessage", dati)
    ephemeral_message_id = risposta.get("ephemeral_message_id")
    if ephemeral_message_id is not None:
        messaggi_effimeri[chiave] = [ephemeral_message_id]
    else:
        logger.warning("Telegram non ha restituito ephemeral_message_id per il messaggio effimero")
    return risposta


async def elimina_messaggi_effimeri(bot, chat_id: int, user_id: int):
    """Elimina gli effimeri registrati per una chat e un utente."""
    chiave = (chat_id, user_id)
    precedenti = messaggi_effimeri.pop(chiave, [])
    for ephemeral_message_id in precedenti:
        try:
            await bot._post(
                "deleteEphemeralMessage",
                {
                    "chat_id": chat_id,
                    "receiver_user_id": user_id,
                    "ephemeral_message_id": ephemeral_message_id,
                },
            )
        except Exception as error:
            logger.warning("Impossibile rimuovere il messaggio effimero %s: %s", ephemeral_message_id, error)


async def rispondi(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                   reply_markup=None, pubblica=False, parse_mode=None):
    """Invia una risposta pubblica o effimera nel gruppo corrente."""
    visibilita = 'pubblica' if pubblica or update.effective_chat.type == 'private' else 'effimera'
    logger.info("Risposta %s a %s: %s", visibilita, nome_utente(update), text.splitlines()[0][:120])
    if pubblica or update.effective_chat.type == 'private':
        return await context.application.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    return await invia_messaggio_effimero(
        context.application.bot,
        update.effective_chat.id,
        update.effective_user.id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def rispondi_callback(query, context: ContextTypes.DEFAULT_TYPE, text: str,
                            reply_markup=None, pubblica=False, parse_mode=None):
    """Invia una risposta effimera a un callback."""
    logger.info("Risposta callback a %s: %s", nome_utente(query), text.splitlines()[0][:120])
    if pubblica:
        return await query.message.reply_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode
        )

    return await invia_messaggio_effimero(
        context.application.bot,
        query.message.chat_id,
        query.from_user.id,
        text,
        reply_markup=reply_markup,
        callback_query_id=query.id,
        parse_mode=parse_mode,
    )


async def nascondi_input_gruppo(update: Update):
    """Rimuove dal gruppo il testo inserito durante una conversazione effimera."""
    if update.effective_chat.type != 'private' and update.message:
        try:
            await update.message.delete()
        except Exception:
            logger.warning("Impossibile rimuovere l'input dell'utente dal gruppo")


async def configura_comandi(application):
    """Registra il menu dei comandi gestiti dal bot."""
    comandi = [
        ('punta', 'Inserisci una puntata'),
        ('modifica', 'Modifica una puntata'),
        ('recap', 'Mostra il recap'),
        ('conteggio', 'Conta i ciclisti'),
        ('sollecita', 'Sollecita chi non ha puntato'),
        ('aiuto', 'Mostra aiuto'),
        ('aggiungigara', 'Crea una gara'),
        ('eliminagara', 'Elimina una gara'),
        ('start', 'Registra utente'),
    ]
    await application.bot.set_my_commands(
        [BotCommand(nome, descrizione)
         for nome, descrizione in comandi]
    )


async def gestione_errore(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Registra gli errori degli update senza interrompere il polling."""
    logger.error("Errore durante la gestione dell'update", exc_info=context.error)

# === COMANDO /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'start')
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    chat_id = update.effective_chat.id
    
    db.aggiungi_utente_gruppo(user_id, username, chat_id)
    
    await rispondi(update, context,
        f"✅ Ciao! Tarlazzi ti da il benvenuto! 🚴\n\n"
        f"Usa /aiuto per vedere tutti i comandi disponibili!"
    )

# === COMANDO /aiuto ===
async def aiuto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'aiuto')
    testo_aiuto = (
        "🚴 <b>Tarlazzi Bot - Comandi Disponibili</b> 🚴\n\n"
        
        "<b>🎯 Gestione Puntate</b>\n"
        "/punta - Inserisci la tua puntata per una gara\n"
        "/modifica - Modifica una puntata già inserita\n\n"

        "<b>📋 Gestione Gare</b>\n"
        "/aggiungigara - Crea una nuova gara\n"
        "/eliminagara - Elimina una gara esistente\n"
        "/recap - Mostra il riepilogo delle puntate per le gare attive → notifica tutti!\n"
        "/conteggio - Conta quante volte ogni ciclista appare nelle puntate → notifica tutti!\n\n"
        "/sollecita - Sollecita chi non ha ancora inserito una puntata\n\n"
        
        "<b>💡 Come funziona?</b>\n"
        "• Inserisci i ciclisti separati da spazio, ; oppure /\n"
        "• Puoi inserire anche 1 o 2 ciclisti (gli altri saranno X)\n"
        "• Riceverai un reminder 1 ora prima della scadenza\n"
        "• Le gare vengono eliminate automaticamente a fine giornata\n\n"
        
        "Usa /aiuto in qualsiasi momento per vedere questo messaggio!"
    )
    
    await rispondi(update, context, testo_aiuto, parse_mode=ParseMode.HTML)

# === COMANDO /aggiungigara ===
async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'aggiungigara')
    await rispondi(update, context, "Inserisci la descrizione della gara:")
    return DESCRIZIONE


def tastiera_date(oggi: str, domani: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Oggi", callback_data=f"data_oggi_{oggi}"),
            InlineKeyboardButton("Domani", callback_data=f"data_domani_{domani}"),
        ],
        [InlineKeyboardButton("Altra data", callback_data="data_personalizzata")],
    ])


def tastiera_ore() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("14:00", callback_data="ora_14:00")],
        [InlineKeyboardButton("Altro orario", callback_data="ora_personalizzata")],
    ])

async def ricevi_descrizione(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Salva la descrizione!
    context.user_data['descrizione'] = update.message.text.strip()
    logger.info("%s ha inserito la descrizione della gara: %s", nome_utente(update), context.user_data['descrizione'])
    await nascondi_input_gruppo(update)
    # Prosegui chiedendo la data
    oggi = datetime.now().strftime("%d/%m/%Y")
    domani = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    await rispondi(update, context,
        "Scegli la data:",
        reply_markup=tastiera_date(oggi, domani),
    )
    return DATA


async def seleziona_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "data_personalizzata":
        if update.effective_chat.type == 'private':
            await query.edit_message_text("Scrivi la data in formato GG/MM/AAAA:")
        else:
            await invia_messaggio_effimero(
                context.application.bot,
                query.message.chat_id,
                query.from_user.id,
                "Scrivi la data in formato GG/MM/AAAA:",
                callback_query_id=query.id,
            )
        return DATA

    testo = query.data.rsplit("_", 1)[1]
    data = datetime.strptime(testo, "%d/%m/%Y").date()
    context.user_data['data'] = data
    messaggio_ora = "Scegli l'ora di scadenza:"
    if update.effective_chat.type == 'private':
        await query.edit_message_text(messaggio_ora, reply_markup=tastiera_ore())
    else:
        await invia_messaggio_effimero(
            context.application.bot,
            query.message.chat_id,
            query.from_user.id,
            messaggio_ora,
            reply_markup=tastiera_ore(),
            callback_query_id=query.id,
        )
    return ORA

async def ricevi_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text.strip()
    logger.info("%s ha inserito la data: %s", nome_utente(update), testo)
    await nascondi_input_gruppo(update)

    # Se utente sceglie "Personalizzata", chiedi di scrivere la data a mano
    if testo.lower().startswith("personal"):
        await rispondi(update, context, "Scrivi la data in formato GG/MM/AAAA", reply_markup=ReplyKeyboardRemove())
        return DATA  # Resta in questo stato

    # Tenta di interpretare la data
    try:
        data = datetime.strptime(testo, "%d/%m/%Y").date()
        # Validazione anticipata: la data deve essere oggi o nel futuro
        if data < datetime.today().date():
            await rispondi(update, context, "La data inserita è nel passato. Riprova:", reply_markup=ReplyKeyboardRemove())
            return DATA

        context.user_data['data'] = data
        await rispondi(update, context,
            "Scegli l'ora di scadenza oppure scrivila in formato HH:MM:",
            reply_markup=tastiera_ore(),
        )
        return ORA

    except ValueError:
        await rispondi(update, context, "Formato data non valido. Riprova con GG/MM/AAAA:", reply_markup=ReplyKeyboardRemove())
        return DATA


async def seleziona_ora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ora_personalizzata":
        if update.effective_chat.type == 'private':
            await query.edit_message_text("Scrivi l'ora in formato HH:MM:")
        else:
            await invia_messaggio_effimero(
                context.application.bot,
                query.message.chat_id,
                query.from_user.id,
                "Scrivi l'ora in formato HH:MM:",
                callback_query_id=query.id,
            )
        return ORA

    ora_input = query.data.rsplit("_", 1)[1]
    data = context.user_data['data']
    data_scadenza = datetime.combine(
        data,
        datetime.strptime(ora_input, "%H:%M").time(),
    )

    if data_scadenza < datetime.now():
        messaggio = "Le 14:00 di oggi è già trascorsa. Scrivi un altro orario in formato HH:MM:"
        if update.effective_chat.type == 'private':
            await query.edit_message_text(messaggio)
        else:
            await invia_messaggio_effimero(
                context.application.bot,
                query.message.chat_id,
                query.from_user.id,
                messaggio,
                callback_query_id=query.id,
            )
        return ORA

    await finalizza_gara(
        context,
        get_chat_id_effettivo(update),
        data_scadenza,
    )
    return ConversationHandler.END


async def finalizza_gara(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                         data_scadenza: datetime, update: Update | None = None):
    gara_id = db.aggiungi_gara(context.user_data['descrizione'], data_scadenza, chat_id)
    logger.info("Gara aggiunta: id=%s, nome=%s, scadenza=%s, chat=%s", gara_id, context.user_data['descrizione'], data_scadenza, chat_id)

    if data_scadenza - timedelta(hours=1) > datetime.now():
        scheduler.add_job(
            invia_reminder,
            'date',
            run_date=data_scadenza - timedelta(hours=1),
            args=[context.application.bot, chat_id, gara_id]
        )

    fine_giornata = datetime.combine(data_scadenza.date(), datetime.max.time())
    scheduler.add_job(
        elimina_gara_automatica,
        'date',
        run_date=fine_giornata,
        args=[context.application.bot, gara_id],
    )

    testo = (
        f"✅ Gara aggiunta!\n\n"
        f"📋 {context.user_data['descrizione']}\n"
        f"⏰ Scadenza: {data_scadenza.strftime('%d/%m/%Y alle %H:%M')}\n"
        f"🗑️ Verrà eliminata automaticamente a fine giornata"
    )
    if update and update.effective_chat.type != 'private':
        await elimina_messaggi_effimeri(
            context.application.bot,
            chat_id,
            update.effective_user.id,
        )
    if update and update.effective_chat.type == 'private' and update.message:
        await update.message.reply_text(
            testo,
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.HTML,
        )
    else:
        await context.application.bot.send_message(
            chat_id,
            testo,
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.HTML,
        )
    logger.info("Risposta pubblica: gara aggiunta nella chat %s", chat_id)


async def ricevi_ora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ora_input = update.message.text.strip()
    logger.info("%s ha inserito l'ora: %s", nome_utente(update), ora_input)
    await nascondi_input_gruppo(update)
    try:
        ora = datetime.strptime(ora_input, "%H:%M").time()
        data = context.user_data['data']
        data_scadenza = datetime.combine(data, ora)

        # Validazione: data+ora non nel passato
        if data_scadenza < datetime.now():
            await rispondi(update, context, "Attenzione, data/ora già trascorse. Riscrivi solo l'ora valida:", reply_markup=ReplyKeyboardRemove())
            return ORA

        await finalizza_gara(context, get_chat_id_effettivo(update), data_scadenza, update)

        return ConversationHandler.END

    except ValueError:
        await rispondi(update, context, "Formato ora non valido. Scrivi HH:MM (esempio: 15:30)", reply_markup=ReplyKeyboardRemove())
        return ORA
    
# === COMANDO /eliminagara ===
async def rimuovi_recap(bot, gara):
    """Scollega e cancella il recap associato alla gara."""
    message_id = gara[4]
    if message_id is None:
        return

    try:
        await bot.unpin_chat_message(gara[3], message_id)
    except Exception as error:
        logger.warning("Impossibile togliere il recap dai messaggi fissati: %s", error)

    try:
        await bot.delete_message(gara[3], message_id)
    except Exception as error:
        logger.warning("Impossibile cancellare il recap: %s", error)


async def elimina_gara(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'eliminagara')
    chat_id = get_chat_id_effettivo(update)
    gare = db.get_gare_attive(chat_id)
    if not gare:
        await rispondi(update, context, "⚠️ Nessuna gara attiva da eliminare.")
        return ConversationHandler.END

    if len(gare) == 1:
        gara_id = gare[0][0]
        puntate = db.get_puntate_gara(gara_id)
        if puntate:
            text = f"Sono presenti {len(puntate)} puntate. Vuoi davvero eliminare la gara '{gare[0][1]}'?"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Sì, elimina", callback_data=f"conferma_elimina_{gara_id}")],
                [InlineKeyboardButton("No, annulla", callback_data="annulla_elimina")]
            ])
            await rispondi(update, context, text, reply_markup=keyboard)
            return CONFERMA_ELIMINA
        else:
            gara = db.get_gara(gara_id)
            await rimuovi_recap(context.application.bot, gara)
            db.elimina_gara(gara_id)
            await rispondi(update, context, f"✅ Gara eliminata: {gare[0][1]}")
            return ConversationHandler.END

    reply_markup = genera_lista_gare(gare, "elimina")
    await rispondi(update, context, "Seleziona la gara da eliminare:", reply_markup=reply_markup)
    return CONFERMA_ELIMINA


async def elimina_gara_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    logger.info("%s ha selezionato l'azione %s", nome_utente(update), data)
    
    if data == "annulla_elimina":
        await rispondi_callback(query, context, "❌ Eliminazione gara annullata.")
        return ConversationHandler.END
    
    if data.startswith("elimina_"):
        gara_id = int(data.split("_")[1])
        puntate = db.get_puntate_gara(gara_id)
        if puntate:
            text = f"Sono presenti {len(puntate)} puntate. Vuoi davvero eliminare questa gara?"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Sì, elimina", callback_data=f"conferma_elimina_{gara_id}")],
                [InlineKeyboardButton("No, annulla", callback_data="annulla_elimina")]
            ])
            await rispondi_callback(query, context, text, reply_markup=keyboard)
            return CONFERMA_ELIMINA
        else:
            gara = db.get_gara(gara_id)
            await rimuovi_recap(context.application.bot, gara)
            db.elimina_gara(gara_id)
            logger.info("%s ha eliminato %s (id=%s)", nome_utente(update), gara[1], gara_id)
            await rispondi_callback(query, context, f"✅ {gara[1]} eliminata")
            return ConversationHandler.END
    
    if data.startswith("conferma_elimina_"):
        gara_id = int(data.split("_")[2])
        gara = db.get_gara(gara_id)
        await rimuovi_recap(context.application.bot, gara)
        db.elimina_gara(gara_id)
        logger.info("%s ha eliminato %s (id=%s)", nome_utente(update), gara[1], gara_id)
        await rispondi_callback(query, context, f"✅ {gara[1]} eliminata")
        return ConversationHandler.END


# === COMANDO /punta ===
async def punta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'punta')
    chat_id = get_chat_id_effettivo(update)
    is_private = update.effective_chat.type == 'private'
    
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await rispondi(update, context, "⚠️ Nessuna gara attiva al momento.")
        return ConversationHandler.END
    
    if len(gare) == 1:
        gara_id = gare[0][0]
        context.user_data['gara_id_punta'] = gara_id
        context.user_data['gruppo_id'] = chat_id
        context.user_data['is_private'] = is_private
        context.user_data['is_ephemeral_flow'] = not is_private
        
        messaggio = f"📋 Gara: {gare[0][1]}\n\n"
        messaggio += (
            "Inserisci i nomi dei ciclisti separati da spazio, punto e virgola o slash.\n"
        )
        if is_private:
            await update.message.reply_text(messaggio)
        else:
            await invia_messaggio_effimero(
                context.application.bot,
                chat_id,
                update.effective_user.id,
                messaggio,
            )
        return CICLISTI
    
    reply_markup = genera_lista_gare(gare, "punta")
    await rispondi(update, context, "Seleziona la gara:", reply_markup=reply_markup)
    context.user_data['gruppo_id'] = chat_id
    context.user_data['is_private'] = is_private
    context.user_data['is_ephemeral_flow'] = not is_private
    return CICLISTI

async def seleziona_gara_punta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    gara_id = int(query.data.split('_')[1])
    logger.info("%s ha selezionato gara id=%s per la puntata", nome_utente(update), gara_id)
    context.user_data['gara_id_punta'] = gara_id
    
    messaggio = (
        "Inserisci i nomi dei ciclisti separati da spazio, punto e virgola o slash.\n"
    )
    
    if context.user_data.get('is_private'):
        await query.edit_message_text(messaggio)
    else:
        await query.answer()
        await invia_messaggio_effimero(
            context.application.bot,
            query.message.chat_id,
            query.from_user.id,
            messaggio,
        )
    return CICLISTI

async def ricevi_ciclisti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text.strip()
    logger.info("%s ha inserito la puntata: %s", nome_utente(update), testo)
    
    # Sostituisci tutti i separatori ',', ';', '/' con uno spazio
    testo = re.sub(r'[;,/]+', ' ', testo)
    
    # Ora split su spazi (uno o più)
    ciclisti = re.split(r'\s+', testo)
    
    # Rimuovi eventuali stringhe vuote
    ciclisti = [c.strip() for c in ciclisti if c.strip()]
    
    while len(ciclisti) < 3:
        ciclisti.append('X')
    
    if len(ciclisti) > 3:
        await rispondi(update, context,
            f"⚠️ Devi inserire massimo 3 ciclisti! Ne hai inseriti {len(ciclisti)}.\n"
            "Riprova:"
        )
        return CICLISTI
    
    ciclisti_non_x = [c for c in ciclisti if c != 'X']
    if len(ciclisti_non_x) != len(set(ciclisti_non_x)):
        await rispondi(update, context,
            "⚠️ Non puoi puntare lo stesso ciclista più volte!\n"
            "Riprova:"
        )
        return CICLISTI
    
    db.aggiungi_puntata(
        context.user_data['gara_id_punta'],
        update.effective_user.id,
        update.effective_user.username or update.effective_user.first_name,
        ciclisti
    )
    gara = db.get_gara(context.user_data['gara_id_punta'])
    logger.info("Puntata di %s aggiunta a %s: %s", nome_utente(update), gara[1], ', '.join(ciclisti))
    
    output_ciclisti = []
    for i, c in enumerate(ciclisti, 1):
        if c == 'X':
            output_ciclisti.append(f"🚴 {i}. ❓")
        else:
            output_ciclisti.append(f"🚴 {i}. {c}")
    
    is_private = context.user_data.get('is_private', False)
    
    if is_private:
        await update.message.reply_text(
            f"✅ Puntata registrata per il gruppo!\n\n" + "\n".join(output_ciclisti)
        )
    elif context.user_data.get('is_ephemeral_flow'):
        await invia_messaggio_effimero(
            context.application.bot,
            context.user_data.get('gruppo_id', update.effective_chat.id),
            update.effective_user.id,
            f"✅ Puntata registrata!\n\n" + "\n".join(output_ciclisti),
        )
    else:
        # Risposta breve in gruppo
        await rispondi(update, context, "✅ Puntata registrata! Controlla i dettagli in privato.")
    
    # aggiorna recap nel gruppo come prima
    await aggiorna_recap(
        context.user_data.get('gruppo_id', update.effective_chat.id),
        context.user_data['gara_id_punta'],
        context.application.bot,
    )
    
    return ConversationHandler.END

# === COMANDO /modifica ===
async def modifica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'modifica')
    chat_id = get_chat_id_effettivo(update)
    is_private = update.effective_chat.type == 'private'
    
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await rispondi(update, context, "⚠️ Nessuna gara attiva al momento.")
        return ConversationHandler.END
    
    if len(gare) == 1:
        gara_id = gare[0][0]
        context.user_data['gara_id_punta'] = gara_id
        context.user_data['gruppo_id'] = chat_id
        context.user_data['is_private'] = is_private
        
        messaggio = f"📋 Gara: {gare[0][1]}\n\n"
        if is_private:
            messaggio += "💬 Stai modificando dal privato per il gruppo\n\n"
        messaggio += (
            "Inserisci i nuovi ciclisti separati da spazio, punto e virgola o slash.\n"
            "Esempio: Pogacar Roglic Vingegaard"
        )
        if is_private:
            await rispondi(update, context, messaggio)
        else:
            await invia_messaggio_effimero(
                context.application.bot,
                chat_id,
                update.effective_user.id,
                messaggio,
            )
        return CICLISTI
    
    reply_markup = genera_lista_gare(gare, "modifica")
    await rispondi(update, context, "Seleziona la gara da modificare:", reply_markup=reply_markup)
    context.user_data['gruppo_id'] = chat_id
    context.user_data['is_private'] = is_private
    context.user_data['is_ephemeral_flow'] = not is_private
    return CICLISTI

async def seleziona_gara_modifica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    gara_id = int(query.data.split('_')[1])
    logger.info("%s ha selezionato gara id=%s per la modifica", nome_utente(update), gara_id)
    context.user_data['gara_id_punta'] = gara_id
    
    messaggio = (
        "Inserisci i nuovi ciclisti separati da spazio, punto e virgola o slash.\n"
        "Esempio: Pogacar Roglic Vingegaard"
    )
    
    if context.user_data.get('is_private'):
        messaggio = "💬 Stai modificando dal privato per il gruppo\n\n" + messaggio
    
    if context.user_data.get('is_private'):
        await query.edit_message_text(messaggio)
    else:
        await query.answer()
        await invia_messaggio_effimero(
            context.application.bot,
            query.message.chat_id,
            query.from_user.id,
            messaggio,
        )
    return CICLISTI

async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await rispondi(update, context, "Operazione annullata.")
    return ConversationHandler.END

# === COMANDO /recap ===
async def recap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'recap')
    chat_id = get_chat_id_effettivo(update)
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await rispondi(update, context, "⚠️ Nessuna gara attiva.")
        return
    
    if len(gare) == 1:
        await invia_recap(
            chat_id, gare[0][0], context.application.bot, update.message,
            forza_nuovo=True,
        )
        return
    
    for gara in gare:
        gara_id, descrizione, scadenza = gara
        await invia_recap(
            chat_id, gara_id, context.application.bot, update.message,
            forza_nuovo=True,
        )

def testo_recap(gara, puntate) -> str:
    recap_text = f"<b>• {escape(gara[1])} •</b>\n\n"

    if not puntate:
        return recap_text + "⚠️ Nessuna puntata ancora inserita."

    for name, c1, c2, c3 in sorted(puntate, key=lambda puntata: puntata[0].casefold()):
        recap_text += f"{escape(name)}: {escape(c1)}, {escape(c2)}, {escape(c3)}\n"
    return recap_text


async def invia_recap(chat_id: int, gara_id: int, bot, message=None, forza_nuovo=False):
    gara = db.get_gara(gara_id)
    puntate = db.get_puntate_gara(gara_id)
    recap_text = testo_recap(gara, puntate)
    
    if message and message.chat.type == 'private':
        await message.reply_text(recap_text, parse_mode=ParseMode.HTML)
        return
    elif not forza_nuovo and gara[4]:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=gara[4],
                text=recap_text,
                parse_mode=ParseMode.HTML,
            )
            logger.info("Recap aggiornato per gara id=%s nella chat %s", gara_id, chat_id)
            return
        except Exception as error:
            logger.warning("Impossibile aggiornare il recap esistente: %s", error)

    if forza_nuovo and gara[4]:
        try:
            await bot.unpin_chat_message(chat_id, gara[4])
            await bot.delete_message(chat_id, gara[4])
        except Exception as error:
            logger.warning("Impossibile rimuovere il recap precedente: %s", error)

    sent_message = await bot.send_message(chat_id, recap_text, parse_mode=ParseMode.HTML)
    logger.info("Recap inviato per gara id=%s nella chat %s", gara_id, chat_id)

    try:
        await bot.pin_chat_message(chat_id, sent_message.message_id, disable_notification=True)
        db.update_message_id(gara_id, sent_message.message_id)
    except Exception as e:
        logger.error(f"Errore nel pinnare il messaggio: {e}")


async def aggiorna_recap(chat_id: int, gara_id: int, bot):
    await invia_recap(chat_id, gara_id, bot)


# === COMANDO /sollecita ===
async def sollecita(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'sollecita')
    gare = db.get_gare_attive(get_chat_id_effettivo(update))

    if not gare:
        await rispondi(update, context, "⚠️ Nessuna gara attiva al momento.")
        return ConversationHandler.END

    if len(gare) == 1:
        await elimina_messaggi_effimeri(
            context.application.bot,
            get_chat_id_effettivo(update),
            update.effective_user.id,
        )
        await invia_sollecito(get_chat_id_effettivo(update), gare[0][0], context.application.bot)
        return ConversationHandler.END

    await rispondi(
        update, context, "Seleziona la gara da sollecitare:",
        reply_markup=genera_lista_gare(gare, "sollecita"),
    )
    return SELEZIONA_GARA_SOLLECITA


async def seleziona_gara_sollecita(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    gara_id = int(query.data.split('_')[1])
    await elimina_messaggi_effimeri(
        context.application.bot,
        query.message.chat_id,
        query.from_user.id,
    )
    await invia_sollecito(query.message.chat_id, gara_id, context.application.bot)
    await query.answer()
    return ConversationHandler.END


async def invia_sollecito(chat_id: int, gara_id: int, bot):
    gara = db.get_gara(gara_id)
    utenti_registrati = db.get_utenti_gruppo(chat_id)
    if not utenti_registrati:
        db.load_users_from_file(chat_id)
        utenti_registrati = db.get_utenti_gruppo(chat_id)

    user_ids_puntato = set(db.get_user_ids_che_hanno_puntato(gara_id))
    utenti_mancanti = [utente for utente in utenti_registrati if utente[0] not in user_ids_puntato]
    logger.info(
        "Sollecito per gara id=%s (%s): utenti mancanti=%s",
        gara_id,
        gara[1],
        ', '.join(display_name for _, display_name in utenti_mancanti) or 'nessuno',
    )

    if not utenti_mancanti:
        await bot.send_message(chat_id, f"✅ Tutti gli utenti hanno già puntato per {gara[1]}")
        return

    menzioni = [
        f'<a href="tg://user?id={user_id}">{escape(display_name)}</a>'
        for user_id, display_name in utenti_mancanti
    ]
    scadenza = datetime.fromisoformat(str(gara[2])).strftime('%d/%m/%Y alle %H:%M')
    messaggio = (
        f"🚩 <b>{escape(gara[1])}</b>\n\n"
        "Devono ancora puntare:\n"
        f"{chr(10).join(menzioni)}\n\n"
        f"Usa /punta per inserire la tua puntata entro le {scadenza}."
    )
    await bot.send_message(chat_id, messaggio, parse_mode=ParseMode.HTML)

# === COMANDO /conteggio ===
async def conteggio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registra_richiesta(update, 'conteggio')
    chat_id = get_chat_id_effettivo(update)
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await rispondi(update, context, "⚠️ Nessuna gara attiva al momento.")
        return ConversationHandler.END
    
    if len(gare) == 1:
        gara_id = gare[0][0]
        await mostra_conteggio(
            chat_id, gara_id, context.application.bot, update.effective_user.id,
        )
        return ConversationHandler.END
    
    reply_markup = genera_lista_gare(gare, "conteggio")
    await rispondi(update, context, "Seleziona la gara per il conteggio:", reply_markup=reply_markup)
    return SELEZIONA_GARA_CONTEGGIO

async def seleziona_gara_conteggio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    gara_id = int(query.data.split('_')[1])
    logger.info("%s ha selezionato gara id=%s per il conteggio", nome_utente(update), gara_id)
    chat_id = get_chat_id_effettivo(update)
    await mostra_conteggio(
        chat_id, gara_id, context.application.bot, query.from_user.id,
    )
    return ConversationHandler.END

async def mostra_conteggio(chat_id: int, gara_id: int, bot, user_id: int | None = None):
    gara = db.get_gara(gara_id)
    ciclisti = db.get_tutti_ciclisti_gara(gara_id)

    if user_id is not None:
        await elimina_messaggi_effimeri(bot, chat_id, user_id)
    
    if not ciclisti:
        await bot.send_message(chat_id, "⚠️ Nessuna puntata ancora inserita per questa gara.")
        return
    
    conteggio = Counter(ciclisti)
    ciclisti_ordinati = conteggio.most_common()
    
    testo_conteggio = f"📊 <b>Conteggio ciclisti - {gara[1]}</b>\n\n"
    
    for ciclista, count in ciclisti_ordinati:
        testo_conteggio += f"<b>{count}</b> {ciclista}\n"
    
    await bot.send_message(chat_id, testo_conteggio, parse_mode=ParseMode.HTML)
    logger.info("Conteggio inviato per gara id=%s nella chat %s", gara_id, chat_id)

# === REMINDER AUTOMATICO ===
async def invia_reminder(bot, chat_id: int, gara_id: int):
    gara = db.get_gara(gara_id)
    
    utenti_registrati = db.get_utenti_gruppo(chat_id)
    
    if not utenti_registrati:
        db.load_users_from_file(chat_id)
        utenti_registrati = db.get_utenti_gruppo(chat_id)
    
    user_ids_puntato = db.get_user_ids_che_hanno_puntato(gara_id)
    
    utenti_mancanti = [(uid, uname) for uid, uname in utenti_registrati if uid not in user_ids_puntato]
    
    if not utenti_mancanti:
        return
    
    menzioni = []
    for user_id, display_name in utenti_mancanti:
        menzioni.append(f'<a href="tg://user?id={user_id}">{display_name}</a>')
    
    messaggio = (
        f"⏰ <b>REMINDER</b>: Manca 1 ora alla scadenza della gara '<i>{gara[1]}</i>'!\n\n"
        f"Chi non ha ancora puntato:\n"
        f"{', '.join(menzioni)}\n\n"
        f"Usate /punta per inserire la vostra puntata!"
    )
    
    await bot.send_message(chat_id, messaggio, parse_mode=ParseMode.HTML)
    logger.info("Reminder inviato per gara id=%s nella chat %s", gara_id, chat_id)

# === ELIMINAZIONE AUTOMATICA ===
def elimina_gara_automatica(bot, gara_id: int):
    gara = db.get_gara(gara_id)
    if gara:
        import asyncio
        asyncio.run(rimuovi_recap(bot, gara))
    db.elimina_gara(gara_id)
    logger.info(f"Gara {gara_id} eliminata automaticamente")

# === MAIN ===
def main():
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.start()
    
    application = Application.builder().token(TOKEN).post_init(configura_comandi).build()
    application.add_error_handler(gestione_errore)
    
    # Comandi base
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('aiuto', aiuto))
    application.add_handler(CommandHandler('help', aiuto))
    
    # Conversation handlers
    conv_aggiungi = ConversationHandler(
    entry_points=[CommandHandler('aggiungigara', aggiungi)],
    allow_reentry=True,
    states={
        DESCRIZIONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_descrizione)],
        DATA: [
            CallbackQueryHandler(seleziona_data, pattern='^data_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_data),
        ],
        ORA: [
            CallbackQueryHandler(seleziona_ora, pattern='^ora_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_ora),
        ],
    },
    fallbacks=[CommandHandler('cancella', cancella)]
    )
    
    conv_elimina = ConversationHandler(
    entry_points=[CommandHandler('eliminagara', elimina_gara)],
    allow_reentry=True,
    states={
        CONFERMA_ELIMINA: [CallbackQueryHandler(elimina_gara_callback, pattern='^(elimina_|conferma_elimina_|annulla_elimina)')]
    },
    fallbacks=[CommandHandler('cancella', cancella)]
    )

    conv_punta = ConversationHandler(
        entry_points=[CommandHandler('punta', punta)],
        allow_reentry=True,
        states={
            CICLISTI: [
                CallbackQueryHandler(seleziona_gara_punta, pattern='^punta_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_ciclisti)
            ],
        },
        fallbacks=[CommandHandler('cancella', cancella)]
    )
    
    conv_modifica = ConversationHandler(
        entry_points=[CommandHandler('modifica', modifica)],
        allow_reentry=True,
        states={
            CICLISTI: [
                CallbackQueryHandler(seleziona_gara_modifica, pattern='^modifica_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_ciclisti)
            ],
        },
        fallbacks=[CommandHandler('cancella', cancella)]
    )
    
    conv_conteggio = ConversationHandler(
        entry_points=[CommandHandler('conteggio', conteggio)],
        allow_reentry=True,
        states={
            SELEZIONA_GARA_CONTEGGIO: [
                CallbackQueryHandler(seleziona_gara_conteggio, pattern='^conteggio_')
            ],
        },
        fallbacks=[CommandHandler('cancella', cancella)]
    )

    conv_sollecita = ConversationHandler(
        entry_points=[CommandHandler('sollecita', sollecita)],
        allow_reentry=True,
        states={
            SELEZIONA_GARA_SOLLECITA: [
                CallbackQueryHandler(seleziona_gara_sollecita, pattern='^sollecita_')
            ],
        },
        fallbacks=[CommandHandler('cancella', cancella)]
    )
    
    application.add_handler(conv_aggiungi)
    application.add_handler(conv_elimina)
    application.add_handler(conv_punta)
    application.add_handler(conv_modifica)
    application.add_handler(conv_conteggio)
    application.add_handler(conv_sollecita)
    application.add_handler(CommandHandler('recap', recap))
    application.add_handler(CommandHandler('eliminagara', elimina_gara))
    application.add_handler(CallbackQueryHandler(elimina_gara_callback, pattern="^elimina_"))

    
    logger.info("Bot avviato!")
    application.run_polling()

if __name__ == '__main__':
    main()
