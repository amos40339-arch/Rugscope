import os
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from pydub import AudioSegment

# --- SETUP ---
# These must be set in Render's "Environment Variables" dashboard
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

# --- WEB SERVER (For Render/UptimeRobot) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "ChainVigil is Operational", 200

def run_flask():
    # Render provides a PORT environment variable automatically
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- BOT LOGIC ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "🛡️ **ChainVigil Operational**\n\n"
        "Send a chat log for a **Scam Audit**.\n"
        "Send a voice note for an **Audio Audit**.\n"
        "Use /news for **Market Impact Analysis**."
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_log = update.message.text
    # Llama-3-70b for heavy logic
    response = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": "You are a ruthless crypto security auditor. Analyze this log for 'rug pull' red flags, social engineering, and fake liquidity claims. Be blunt."},
                  {"role": "user", "content": chat_log}],
        temperature=0.1
    )
    await update.message.reply_text(f"🔍 **Audit Report:**\n\n{response.choices[0].message.content}")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📥 Processing audio...")
    file = await context.bot.get_file(update.message.voice.file_id)
    await file.download_to_drive("voice.ogg")
    
    # Convert OGG to WAV
    audio = AudioSegment.from_ogg("voice.ogg")
    audio.export("voice.wav", format="wav")

    # Transcribe using Groq Whisper
    with open("voice.wav", "rb") as f:
        transcript = client.audio.transcriptions.create(
            file=("voice.wav", f.read()),
            model="whisper-large-v3",
        )

    # Scrutinize the transcript
    audit = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": "Analyze this audio transcript for crypto fraud or deceptive marketing."},
                  {"role": "user", "content": transcript.text}]
    )
    await update.message.reply_text(f"🎙️ **Transcript:** {transcript.text}\n\n⚖️ **Verdict:** {audit.choices[0].message.content}")

async def get_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Public Crypto News API
    res = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN").json()
    latest = res['Data'][0]
    
    analysis = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "system", "content": "Analyze the market impact of this news. Bullish or Bearish? Why?"},
                  {"role": "user", "content": f"{latest['title']}: {latest['body']}"}]
    )
    await update.message.reply_text(f"📰 **Latest News:** {latest['title']}\n\n📊 **Impact:** {analysis.choices[0].message.content}")

# --- EXECUTION ---
if __name__ == '__main__':
    # Thread 1: Keep-Alive Server
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Thread 2: Telegram Bot
    bot_app = ApplicationBuilder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("news", get_news))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    
    print("System booting...")
    bot_app.run_polling()
