import logging
import re
from datetime import datetime, timedelta
from collections import Counter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.constants import ParseMode
from apscheduler.schedulers.background import BackgroundScheduler
from database import Database

# Configurazione logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.WARNING)
logger = logging.getLogger(__name__)

# Stati per conversation handler
DESCRIZIONE, DATA, ORA, CICLISTI, SELEZIONA_GARA_CONTEGGIO = range(5)
CONFERMA_ELIMINA = 10

# === CONFIGURAZIONE - DA MODIFICARE ===
TOKEN = '1204096884:AAGFnc6tFireMOvl-axjf-IZr7A5OOWGz8g'  # Sostituisci con il token da @BotFather
GRUPPO_CHAT_ID = -4847618655  # Sostituisci dopo aver usato /getchatid nel gruppo

db = Database()

# === FUNZIONI HELPER ===
def get_chat_id_effettivo(update: Update) -> int:
    """Restituisce il chat_id corretto: gruppo hardcodato se in privato, altrimenti la chat corrente"""
    if update.effective_chat.type == 'private':
        return GRUPPO_CHAT_ID
    return update.effective_chat.id

# === Ottieni Group ID ===

async def get_chat_id(update, context):
    chat_id = update.effective_chat.id
    chat_title = getattr(update.effective_chat, "title", "(nessuno)")
    await update.message.reply_text(f"ID gruppo: <code>{chat_id}</code>\nNome: {chat_title}", parse_mode="HTML")


# === COMANDO /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    chat_id = update.effective_chat.id
    
    db.aggiungi_utente_gruppo(user_id, username, chat_id)
    
    await update.message.reply_text(
        f"✅ Ciao! Tarlazzi ti da il benvenuto! 🚴\n\n"
        f"Usa /aiuto per vedere tutti i comandi disponibili!"
    )

# === COMANDO /getchatid (temporaneo per setup) ===
async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    chat_title = update.effective_chat.title if hasattr(update.effective_chat, 'title') else "Chat Privata"
    
    info = (
        f"📋 <b>Informazioni Chat</b>\n\n"
        f"🆔 Chat ID: <code>{chat_id}</code>\n"
        f"📱 Tipo: {chat_type}\n"
        f"👥 Nome: {chat_title}\n\n"
        f"💡 Copia il Chat ID e inseriscilo nel codice come GRUPPO_CHAT_ID!"
    )
    
    await update.message.reply_text(info, parse_mode=ParseMode.HTML)

# === COMANDO /getid ===
async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    info = (
        f"📋 <b>Le tue informazioni:</b>\n\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"👤 Nome: {user.first_name}"
    )
    
    if user.last_name:
        info += f" {user.last_name}"
    
    if user.username:
        info += f"\n📱 Username: @{user.username}"
    else:
        info += f"\n⚠️ Non hai uno username impostato"
    
    info += "\n\n💡 Copia l'User ID e comunicalo all'admin per users.json"
    
    await update.message.reply_text(info, parse_mode=ParseMode.HTML)

# === COMANDO /loadusers ===
async def load_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id_effettivo(update)
    db.load_users_from_file(chat_id)
    await update.message.reply_text("✅ Utenti caricati dal file users.json!")

# === COMANDO /aiuto ===
async def aiuto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo_aiuto = (
        "🚴 <b>Tarlazzi Bot - Comandi Disponibili</b> 🚴\n\n"
        
        "<b>📋 Gestione Gare</b>\n"
        "/aggiungi - Crea una nuova gara (descrizione, data e ora)\n"
        "/eliminagara - Elimina una gara esistente\n"
        "/recap - Mostra il riepilogo delle puntate per le gare attive\n"
        "/conteggio - Conta quante volte ogni ciclista appare nelle puntate\n\n"
        
        "<b>🎯 Gestione Puntate</b>\n"
        "/punta - Inserisci la tua puntata per una gara (max 3 ciclisti)\n"
        "/modifica - Modifica una puntata già inserita\n\n"
        
        "<b>👥 Gestione Utenti</b>\n"
        "/start - Registrati al bot (obbligatorio per ricevere reminder)\n"
        "/getid - Ottieni il tuo User ID per la configurazione\n\n"
        
        "<b>💡 Come funziona?</b>\n"
        "• Puoi usare i comandi sia nel gruppo che in privato\n"
        "• Inserisci i ciclisti separati da spazio, ; oppure /\n"
        "• Puoi inserire anche 1 o 2 ciclisti (gli altri saranno X)\n"
        "• Riceverai un reminder 1 ora prima della scadenza\n"
        "• Le gare vengono eliminate automaticamente a fine giornata\n\n"
        
        "<b>📞 Comandi Admin</b>\n"
        "/loadusers - Carica la lista utenti dal file users.json\n"
        "/getchatid - Ottieni il Chat ID del gruppo (per configurazione)\n\n"
        
        "Usa /aiuto in qualsiasi momento per vedere questo messaggio!"
    )
    
    if update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text(
            "📬 Ti ho inviato l'elenco dei comandi in privato!",
            reply_to_message_id=update.message.message_id
        )
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=testo_aiuto,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            await update.message.reply_text(
                "⚠️ Non posso inviarti messaggi privati. "
                "Avvia una chat con me in privato prima usando /start, "
                "poi riprova /aiuto nel gruppo."
            )
    else:
        await update.message.reply_text(testo_aiuto, parse_mode=ParseMode.HTML)

# === COMANDO /aggiungi ===
async def aggiungi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Inserisci la descrizione della gara:")
    return DESCRIZIONE

async def ricevi_descrizione(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Salva la descrizione!
    context.user_data['descrizione'] = update.message.text.strip()
    # Prosegui chiedendo la data
    oggi = datetime.now().strftime("%d/%m/%Y")
    domani = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    tastiera_date = [[oggi, domani, "Personalizzata"]]
    reply_markup = ReplyKeyboardMarkup(tastiera_date, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text(
        "Seleziona la data di scadenza della gara oppure scrivi in formato GG/MM/AAAA:",
        reply_markup=reply_markup
    )
    return DATA

async def ricevi_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text.strip()

    # Se utente sceglie "Personalizzata", chiedi di scrivere la data a mano
    if testo.lower().startswith("personal"):
        await update.message.reply_text("Scrivi la data in formato GG/MM/AAAA:", reply_markup=ReplyKeyboardRemove())
        return DATA  # Resta in questo stato

    # Tenta di interpretare la data
    try:
        data = datetime.strptime(testo, "%d/%m/%Y").date()
        # Validazione anticipata: la data deve essere oggi o nel futuro
        if data < datetime.today().date():
            await update.message.reply_text("La data inserita è nel passato. Riprova:", reply_markup=ReplyKeyboardRemove())
            return DATA

        context.user_data['data'] = data
        # Tastiera orari predefiniti
        orari = [["14:00"], ["Personalizzata"]]
        reply_markup = ReplyKeyboardMarkup(orari, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "Seleziona l'ora di scadenza (HH:MM) oppure premi Personalizzata e scrivi manualmente:",
            reply_markup=reply_markup
        )
        return ORA

    except ValueError:
        await update.message.reply_text("Formato data non valido. Riprova con GG/MM/AAAA:", reply_markup=ReplyKeyboardRemove())
        return DATA

async def ricevi_ora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ora_input = update.message.text.strip()
    if ora_input.lower().startswith("personal"):
        await update.message.reply_text("Inserisci l'ora in formato HH:MM (es. 14:00):", reply_markup=ReplyKeyboardRemove())
        return ORA

    try:
        ora = datetime.strptime(ora_input, "%H:%M").time()
        data = context.user_data['data']
        data_scadenza = datetime.combine(data, ora)

        # Validazione: data+ora non nel passato
        if data_scadenza < datetime.now():
            await update.message.reply_text("Attenzione, data/ora già trascorse. Riscrivi solo l'ora valida:", reply_markup=ReplyKeyboardRemove())
            return ORA

        chat_id = get_chat_id_effettivo(update)

        # Crea gara nel database
        gara_id = db.aggiungi_gara(
            context.user_data['descrizione'],
            data_scadenza,
            chat_id
        )

        # invia messaggio nel gruppo solo se la conversazione è privata
        if update.effective_chat.type == 'private':
            testo_notifica = (
                f"C'è una nuova gara da puntare:\n\n"
                f"📋 <b>{context.user_data['descrizione']}</b>\n"
                f"⏰ Scadenza: {data_scadenza.strftime('%d/%m/%Y alle %H:%M')}"
            )

        if update.effective_chat.type == 'private':
            await context.bot.send_message(chat_id, testo_notifica, parse_mode=ParseMode.HTML)

        # Scheduler: reminder 1 ora prima
        if data_scadenza - timedelta(hours=1) > datetime.now():
            scheduler.add_job(
                invia_reminder,
                'date',
                run_date=data_scadenza - timedelta(hours=1),
                args=[context.application.bot, chat_id, gara_id]
            )

        # Scheduler: eliminazione automatica gara
        fine_giornata = datetime.combine(data_scadenza.date(), datetime.max.time())
        scheduler.add_job(
            elimina_gara_automatica,
            'date',
            run_date=fine_giornata,
            args=[gara_id]
        )

        await update.message.reply_text(
            f"✅ Gara aggiunta!\n\n"
            f"📋 {context.user_data['descrizione']}\n"
            f"⏰ Scadenza: {data_scadenza.strftime('%d/%m/%Y alle %H:%M')}\n"
            f"🗑️ Verrà eliminata automaticamente a fine giornata",
            reply_markup=ReplyKeyboardRemove()
        )

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("Formato ora non valido. Scrivi HH:MM (esempio: 15:30)", reply_markup=ReplyKeyboardRemove())
        return ORA
    
# === COMANDO /eliminagara ===
async def elimina_gara(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id_effettivo(update)
    gare = db.get_gare_attive(chat_id)
    if not gare:
        await update.message.reply_text("⚠️ Nessuna gara attiva da eliminare.")
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
            await update.message.reply_text(text, reply_markup=keyboard)
            return CONFERMA_ELIMINA
        else:
            db.elimina_gara(gara_id)
            await update.message.reply_text(f"✅ Gara eliminata: {gare[0][1]}")
            return ConversationHandler.END

    keyboard = []
    for gara in gare:
        gara_id, descrizione, scadenza = gara
        keyboard.append([InlineKeyboardButton(f"{descrizione} ({scadenza})", callback_data=f"elimina_{gara_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Seleziona la gara da eliminare:", reply_markup=reply_markup)
    return CONFERMA_ELIMINA


async def elimina_gara_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "annulla_elimina":
        await query.edit_message_text("❌ Eliminazione gara annullata.")
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
            await query.edit_message_text(text, reply_markup=keyboard)
            return CONFERMA_ELIMINA
        else:
            db.elimina_gara(gara_id)
            await query.edit_message_text("✅ Gara eliminata!")
            return ConversationHandler.END
    
    if data.startswith("conferma_elimina_"):
        gara_id = int(data.split("_")[2])
        db.elimina_gara(gara_id)
        await query.edit_message_text("✅ Gara eliminata con conferma!")
        return ConversationHandler.END


# === COMANDO /punta ===
async def punta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id_effettivo(update)
    is_private = update.effective_chat.type == 'private'
    
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await update.message.reply_text("⚠️ Nessuna gara attiva al momento.")
        return ConversationHandler.END
    
    if len(gare) == 1:
        gara_id = gare[0][0]
        context.user_data['gara_id_punta'] = gara_id
        context.user_data['gruppo_id'] = chat_id
        context.user_data['is_private'] = is_private
        
        messaggio = f"📋 Gara: {gare[0][1]}\n\n"
        messaggio += (
            "Inserisci i nomi dei ciclisti separati da spazio, punto e virgola o slash.\n"
        )
        await update.message.reply_text(messaggio)
        return CICLISTI
    
    keyboard = []
    for gara in gare:
        gara_id, descrizione, scadenza = gara
        keyboard.append([InlineKeyboardButton(
            f"{descrizione} (scade: {scadenza})",
            callback_data=f"punta_{gara_id}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Seleziona la gara:", reply_markup=reply_markup)
    context.user_data['gruppo_id'] = chat_id
    context.user_data['is_private'] = is_private
    return CICLISTI

async def seleziona_gara_punta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    gara_id = int(query.data.split('_')[1])
    context.user_data['gara_id_punta'] = gara_id
    
    messaggio = (
        "Inserisci i nomi dei ciclisti separati da spazio, punto e virgola o slash.\n"
    )
    
    await query.edit_message_text(messaggio)
    return CICLISTI

async def ricevi_ciclisti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text.strip()
    
    # Sostituisci tutti i separatori ',', ';', '/' con uno spazio
    testo = re.sub(r'[;,/]+', ' ', testo)
    
    # Ora split su spazi (uno o più)
    ciclisti = re.split(r'\s+', testo)
    
    # Rimuovi eventuali stringhe vuote
    ciclisti = [c.strip() for c in ciclisti if c.strip()]
    
    while len(ciclisti) < 3:
        ciclisti.append('X')
    
    if len(ciclisti) > 3:
        await update.message.reply_text(
            f"⚠️ Devi inserire massimo 3 ciclisti! Ne hai inseriti {len(ciclisti)}.\n"
            "Riprova:"
        )
        return CICLISTI
    
    ciclisti_non_x = [c for c in ciclisti if c != 'X']
    if len(ciclisti_non_x) != len(set(ciclisti_non_x)):
        await update.message.reply_text(
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
    else:
        # Risposta breve in gruppo
        await update.message.reply_text("✅ Puntata registrata! Controlla i dettagli in privato.")
        try:
            # Notifica privata
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"✅ Dettagli puntata:\n\n" + "\n".join(output_ciclisti)
            )
        except:
            pass  # se non ha mai avviato chat privata
    
    # aggiorna recap nel gruppo come prima
    await aggiorna_recap(GRUPPO_CHAT_ID, context.user_data['gara_id_punta'], context.application.bot)
    
    return ConversationHandler.END

# === COMANDO /modifica ===
async def modifica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id_effettivo(update)
    is_private = update.effective_chat.type == 'private'
    
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await update.message.reply_text("⚠️ Nessuna gara attiva al momento.")
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
        await update.message.reply_text(messaggio)
        return CICLISTI
    
    keyboard = []
    for gara in gare:
        gara_id, descrizione, scadenza = gara
    # Converti scadenza da stringa a datetime
    dt = datetime.strptime(scadenza, "%Y-%m-%d %H:%M:%S")
    # Formatta come richiesto
    scadenza_formattata = dt.strftime("%d/%m/%y %H:%M")
    
    keyboard.append([InlineKeyboardButton(
        f"{descrizione} (scade: {scadenza_formattata})",
        callback_data=f"punta_{gara_id}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Seleziona la gara da modificare:", reply_markup=reply_markup)
    context.user_data['gruppo_id'] = chat_id
    context.user_data['is_private'] = is_private
    return CICLISTI

async def seleziona_gara_modifica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    gara_id = int(query.data.split('_')[1])
    context.user_data['gara_id_punta'] = gara_id
    
    messaggio = (
        "Inserisci i nuovi ciclisti separati da spazio, punto e virgola o slash.\n"
        "Esempio: Pogacar Roglic Vingegaard"
    )
    
    if context.user_data.get('is_private'):
        messaggio = "💬 Stai modificando dal privato per il gruppo\n\n" + messaggio
    
    await query.edit_message_text(messaggio)
    return CICLISTI

async def cancella(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operazione annullata.")
    return ConversationHandler.END

# === COMANDO /recap ===
async def recap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id_effettivo(update)
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await update.message.reply_text("⚠️ Nessuna gara attiva.")
        return
    
    if len(gare) == 1:
        await invia_recap(chat_id, gare[0][0], context.application.bot, update.message)
        return
    
    for gara in gare:
        gara_id, descrizione, scadenza = gara
        await invia_recap(chat_id, gara_id, context.application.bot, update.message)

async def invia_recap(chat_id: int, gara_id: int, bot, message=None):
    gara = db.get_gara(gara_id)
    puntate = db.get_puntate_gara(gara_id)
    
    recap_text = f"<b>• {gara[1]} •</b>\n\n"
    
    if not puntate:
        recap_text += "⚠️ Nessuna puntata ancora inserita."
    else:
        for puntata in puntate:
            name, c1, c2, c3 = puntata
            recap_text += f"{name}: {c1}, {c2}, {c3}\n"
    
    if message and message.chat.type == 'private':
        await message.reply_text(recap_text, parse_mode=ParseMode.HTML)
    else:
        sent_message = await bot.send_message(chat_id, recap_text, parse_mode=ParseMode.HTML)
        
        try:
            await bot.pin_chat_message(chat_id, sent_message.message_id, disable_notification=True)
            db.update_message_id(gara_id, sent_message.message_id)
        except Exception as e:
            logger.error(f"Errore nel pinnare il messaggio: {e}")

async def aggiorna_recap(chat_id: int, gara_id: int, bot):
    gara = db.get_gara(gara_id)
    
    if gara[4]:
        try:
            await bot.unpin_chat_message(chat_id, gara[4])
            await bot.delete_message(chat_id, gara[4])
        except:
            pass
    
    await invia_recap(chat_id, gara_id, bot)

# === COMANDO /conteggio ===
async def conteggio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = get_chat_id_effettivo(update)
    gare = db.get_gare_attive(chat_id)
    
    if not gare:
        await update.message.reply_text("⚠️ Nessuna gara attiva al momento.")
        return ConversationHandler.END
    
    if len(gare) == 1:
        gara_id = gare[0][0]
        await mostra_conteggio(chat_id, gara_id, context.application.bot, update.message)
        return ConversationHandler.END
    
    keyboard = []
    for gara in gare:
        gara_id, descrizione, scadenza = gara
        keyboard.append([InlineKeyboardButton(
            f"{descrizione} (scade: {scadenza})",
            callback_data=f"conteggio_{gara_id}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Seleziona la gara per il conteggio:", reply_markup=reply_markup)
    return SELEZIONA_GARA_CONTEGGIO

async def seleziona_gara_conteggio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    gara_id = int(query.data.split('_')[1])
    chat_id = get_chat_id_effettivo(update)
    await mostra_conteggio(chat_id, gara_id, context.application.bot, query.message)
    return ConversationHandler.END

async def mostra_conteggio(chat_id: int, gara_id: int, bot, message):
    gara = db.get_gara(gara_id)
    ciclisti = db.get_tutti_ciclisti_gara(gara_id)
    
    if not ciclisti:
        await message.reply_text("⚠️ Nessuna puntata ancora inserita per questa gara.")
        return
    
    conteggio = Counter(ciclisti)
    ciclisti_ordinati = conteggio.most_common()
    
    testo_conteggio = f"📊 <b>Conteggio ciclisti - {gara[1]}</b>\n\n"
    
    for ciclista, count in ciclisti_ordinati:
        testo_conteggio += f"<b>{count}</b> {ciclista}\n"
    
    await message.reply_text(testo_conteggio, parse_mode=ParseMode.HTML)

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

# === ELIMINAZIONE AUTOMATICA ===
def elimina_gara_automatica(gara_id: int):
    db.elimina_gara(gara_id)
    logger.info(f"Gara {gara_id} eliminata automaticamente")

# === MAIN ===
def main():
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.start()
    
    application = Application.builder().token(TOKEN).build()
    
    # Comandi base
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('aiuto', aiuto))
    application.add_handler(CommandHandler('help', aiuto))
    application.add_handler(CommandHandler('getid', get_id))
    application.add_handler(CommandHandler('getchatid', get_chat_id))
    application.add_handler(CommandHandler('loadusers', load_users))
    
    # Conversation handlers
    conv_aggiungi = ConversationHandler(
    entry_points=[CommandHandler('aggiungi', aggiungi)],
    states={
        DESCRIZIONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_descrizione)],
        DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_data)],
        ORA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ricevi_ora)],
    },
    fallbacks=[CommandHandler('cancella', cancella)]
    )
    
    conv_elimina = ConversationHandler(
    entry_points=[CommandHandler('eliminagara', elimina_gara)],
    states={
        CONFERMA_ELIMINA: [CallbackQueryHandler(elimina_gara_callback, pattern='^(elimina_|conferma_elimina_|annulla_elimina)')]
    },
    fallbacks=[CommandHandler('cancella', cancella)]
    )

    conv_punta = ConversationHandler(
        entry_points=[CommandHandler('punta', punta)],
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
        states={
            SELEZIONA_GARA_CONTEGGIO: [
                CallbackQueryHandler(seleziona_gara_conteggio, pattern='^conteggio_')
            ],
        },
        fallbacks=[CommandHandler('cancella', cancella)]
    )
    
    application.add_handler(conv_aggiungi)
    application.add_handler(conv_elimina)
    application.add_handler(conv_punta)
    application.add_handler(conv_modifica)
    application.add_handler(conv_conteggio)
    application.add_handler(CommandHandler('getchatid', get_chat_id))
    application.add_handler(CommandHandler('recap', recap))
    application.add_handler(CommandHandler('eliminagara', elimina_gara))
    application.add_handler(CallbackQueryHandler(elimina_gara_callback, pattern="^elimina_"))

    
    logger.info("Bot avviato!")
    application.run_polling()

if __name__ == '__main__':
    main()
