import os, threading, time, requests
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
    return "RugScope Master: Operational", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- THE STRATEGIC PARTNER BRAIN ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    # Natural Greetings
    if user_text.lower() in ['hello', 'hi', 'hey', 'start']:
        await update.message.reply_text("🎯 RugScope Active. Strategic Partner Mode. I audit AMAs, grill founders, and scan for rugs. Send me data.")
        return

    status_msg = await update.message.reply_text("🕵️ RugScope is calculating your intent...")

    try:
        # STEP 1: DETECT INTENT & PROVIDE ACKNOWLEDGMENT
        # We tell the AI to confirm it understands the specific task
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": (
                    "You are RugScope, an elite Strategic Partner. "
                    "When a user asks to audit something (AMA, project, business), "
                    "FIRST say 'Yes, I am auditing this [task] for you' or 'Yes, I'm prepping you for [task]'. "
                    "THEN, provide a ruthless, blunt analysis. No asterisks (*). "
                    "If auditing an AMA, find avoided questions. If prepping a founder, be a shark. "
                    "If checking a scam, give a Danger Level. Be fast and sharp."
                )},
                {"role": "user", "content": user_text}
            ]
        )

        final_output = response.choices[0].message.content.replace("*", "")
        await status_msg.edit_text(final_output)

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Fault: {e}")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🎙️ Yes, I am analyzing this audio forensics for you...")
    
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        await file.download_to_drive("voice.ogg")
        
        audio = AudioSegment.from_ogg("voice.ogg")
        audio.export("voice.wav", format="wav")

        with open("voice.wav", "rb") as f:
            transcript = client.audio.transcriptions.create(file=("voice.wav", f.read()), model="whisper-large-v3")

        audit = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "Analyze this transcript. Is the speaker deceptive? Audit the business claims. No stars."},
                      {"role": "user", "content": transcript.text}]
        )
        
        clean_audio = audit.choices[0].message.content.replace("*", "")
        await status_msg.edit_text(f"📝 Transcript: {transcript.text}\n\n⚖️ Strategic Verdict:\n{clean_audio}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Audio Audit Failed: {e}")

if __name__ == '__main__':
    # Flask keeps the bot alive on Render
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 5-second sleep to avoid the 409 Conflict error on Render redeploys
    time.sleep(5)
    
    bot_app = ApplicationBuilder().token(TOKEN).build()
    
    # Intent-driven Handlers (No CommandHandlers needed)
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    
    print("RugScope: All Systems Integrated.")
    bot_app.run_polling()
