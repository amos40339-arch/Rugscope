import os, threading, requests
from flask import Flask
from telegram import Update
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
    return "ChainVigil Status: Operational", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- ERROR HANDLER (Fixes the Red Lines) ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"⚠️ Operational Error: {context.error}")

# --- BOT LOGIC ---
async def onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🛡️ **ChainVigil Intelligence Active**\n\n"
        "I don't need commands. Just send me:\n"
        "• **Chat Logs** to audit for scams.\n"
        "• **Voice Notes** for audio-based fraud detection.\n\n"
        "Type **/news** for market impact analysis."
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    # Check if they just said hello/hi/hey
    if user_text.lower() in ['hello', 'hi', 'hey', 'start']:
        await onboarding(update, context)
        return

    # Otherwise, treat it as a Crypto Audit
    response = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": "You are a blunt crypto security auditor. Audit this text for rugs, scams, and deceptive marketing."},
                  {"role": "user", "content": user_text}],
        temperature=0.1
    )
    await update.message.reply_text(f"🔍 **Audit Report:**\n\n{response.choices[0].message.content}")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📥 Processing audio intelligence...")
    file = await context.bot.get_file(update.message.voice.file_id)
    await file.download_to_drive("voice.ogg")
    
    audio = AudioSegment.from_ogg("voice.ogg")
    audio.export("voice.wav", format="wav")

    with open("voice.wav", "rb") as f:
        transcript = client.audio.transcriptions.create(file=("voice.wav", f.read()), model="whisper-large-v3")

    audit = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[{"role": "system", "content": "Analyze this transcript for crypto fraud."},
                  {"role": "user", "content": transcript.text}]
    )
    await update.message.reply_text(f"🎙️ **Transcript:** {transcript.text}\n\n⚖️ **Verdict:** {audit.choices[0].message.content}")

async def get_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = requests.get("https://min-api.cryptocompare.com/data/v2/news/?lang=EN").json()
    latest = res['Data'][0]
    analysis = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "system", "content": "Market impact analysis: Bullish or Bearish?"},
                  {"role": "user", "content": latest['title']}]
    )
    await update.message.reply_text(f"📰 {latest['title']}\n\n📊 **Impact:** {analysis.choices[0].message.content}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    
    bot_app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    bot_app.add_error_handler(error_handler)
    bot_app.add_handler(CommandHandler("news", get_news))
    bot_app.add_handler(CommandHandler("start", onboarding)) # Keep /start just in case
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    
    print("ChainVigil Core Engaged.")
    bot_app.run_polling()
