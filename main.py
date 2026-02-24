import os, threading, requests
from flask import Flask
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from pydub import AudioSegment

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

# --- WEB SERVER (Keep-Alive) ---
@app.route('/')
def home():
    return "RugScope Status: Operational", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- AUTO-SET COMMANDS ---
async def post_init(application):
    """Sets the /news command in the Telegram menu automatically."""
    commands = [BotCommand("news", "Get real-time market impact reports")]
    await application.bot.set_my_commands(commands)

# --- ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"⚠️ Operational Error: {context.error}")

# --- BOT LOGIC ---
async def onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎯 **RugScope Intelligence: Target Locked**\n\n"
        "I am your forensic filter for crypto fraud. I don't need commands; I need data. Send me:\n\n"
        "🛡️ **Chat Logs:** Audit project shills for 'rug' DNA.\n"
        "🎙️ **Voice Notes:** Scan dev audio for deceptive marketing.\n"
        "📰 **/news:** Get real-time market impact reports.\n\n"
        "*Security is the only priority. Send a message to begin.*"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    # Trigger onboarding for greetings
    if user_text.lower() in ['hello', 'hi', 'hey', 'start', '/start']:
        await onboarding(update, context)
        return

    # Treat everything else as an Audit
    await update.message.reply_text("🕵️ **RugScope is analyzing intelligence...**")
    
    response = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": "You are RugScope, a cynical crypto auditor. Audit this text for rugs, scams, and deceptive marketing. Be blunt."},
                  {"role": "user", "content": user_text}],
        temperature=0.1
    )
    await update.message.reply_text(f"🔍 **Audit Report:**\n\n{response.choices[0].message.content}")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📥 **Processing audio intelligence...**")
    file = await context.bot.get_file(update.message.voice.file_id)
    await file.download_to_drive("voice.ogg")
    
    audio = AudioSegment.from_ogg("voice.ogg")
    audio.export("voice.wav", format="wav")

    with open("voice.wav", "rb") as f:
        transcript = client.audio.transcriptions.create(file=("voice.wav", f.read()), model="whisper-large-v3")

    audit = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": "Analyze this transcript for crypto fraud. Be ruthless."},
                  {"role": "user", "content": transcript.text}]
    )
    await update.message.reply_text(f"🎙️ **Transcript:** {transcript.text}\n\n⚖️ **Verdict:** {audit.choices[0].message.content}")

async def get_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN").json()
    latest = res['Data'][0]
    analysis = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "system", "content": "Market impact analysis: Bullish or Bearish? Why?"},
                  {"role": "user", "content": latest['title']}]
    )
    await update.message.reply_text(f"📰 **Latest News:** {latest['title']}\n\n📊 **Impact:** {analysis.choices[0].message.content}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    
    bot_app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    bot_app.add_error_handler(error_handler)
    bot_app.add_handler(CommandHandler("news", get_news))
    bot_app.add_handler(CommandHandler("start", onboarding))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    
    print("RugScope Core Engaged.")
    bot_app.run_polling()
