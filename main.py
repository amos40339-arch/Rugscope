import os, threading, requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq
from pydub import AudioSegment

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

@app.route('/')
def home():
    return "RugScope: Strategic Intelligence Active", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- THE OPERATOR BRAIN ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    if user_text.lower() in ['hello', 'hi', 'hey', 'start']:
        await update.message.reply_text("🎯 RugScope Active. Strategic partner mode engaged. Feed me data.")
        return

    status_msg = await update.message.reply_text("🕵️ RugScope is calculating...")

    try:
        # STEP 1: INTENT DETECTION
        intent_check = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Classify: CRYPTO_AUDIT, BIZ_STRATEGY, or FOUNDER_PREP. Return only the word."},
                {"role": "user", "content": user_text}
            ]
        )
        intent = intent_check.choices[0].message.content.strip()

        # STEP 2: RUTHLESS PROMPT ENGINEERING
        if "BIZ_STRATEGY" in intent:
            role = "You are a brutal Business Strategist. Tear this idea apart. Find where it loses money. Be blunt. Use plain text, NO STARS."
        elif "CRYPTO_AUDIT" in intent:
            role = "You are a cynical forensic auditor. This is a scam audit. Give a 'Danger Level' out of 10. List 3 red flags. Be ruthless. Use plain text, NO STARS."
        elif "FOUNDER_PREP" in intent:
            role = "You are a ruthless VC Investor. Grill the founder. Ask 3 questions that would expose a weak business model. Use plain text, NO STARS."
        else:
            role = "You are an elite Strategic Partner. Give an aggressive, high-level execution response. Use plain text, NO STARS."

        # STEP 3: EXECUTION
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": role},
                {"role": "user", "content": user_text}
            ]
        )

        # Remove all formatting stars for a clean look
        final_output = response.choices[0].message.content.replace("*", "")
        await status_msg.edit_text(final_output)

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Fault: {e}")

# --- AUDIO FORENSICS ---
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("📥 Processing audio forensics...")
    file = await context.bot.get_file(update.message.voice.file_id)
    await file.download_to_drive("voice.ogg")
    
    audio = AudioSegment.from_ogg("voice.ogg")
    audio.export("voice.wav", format="wav")

    with open("voice.wav", "rb") as f:
        transcript = client.audio.transcriptions.create(file=("voice.wav", f.read()), model="whisper-large-v3")

    audit = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": "Analyze transcript for fraud. Be ruthless. Plain text, no stars."},
                  {"role": "user", "content": transcript.text}]
    )
    clean_audio = audit.choices[0].message.content.replace("*", "")
    await status_msg.edit_text(f"🎙️ Transcript: {transcript.text}\n\n⚖️ Verdict: {clean_audio}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot_app = ApplicationBuilder().token(TOKEN).build()
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    bot_app.run_polling()
