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
    return "RugScope: Intent Intelligence Active", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- THE STRATEGIC BRAIN ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    # 1. Natural Onboarding
    if user_text.lower() in ['hello', 'hi', 'hey', 'start']:
        await update.message.reply_text(
            "🎯 RugScope Strategic Intelligence Active.\n\n"
            "I don't use commands. Just talk to me like an operator. I can:\n"
            "• Audit AMA transcripts for lies.\n"
            "• Grill founders on their business models.\n"
            "• Scan for crypto rugs and social engineering.\n"
            "• Advise on general business strategy.\n\n"
            "What's on the table today?"
        )
        return

    status_msg = await update.message.reply_text("🕵️ RugScope is calculating strategy...")

    try:
        # STEP 2: INTENT DETECTION
        # The AI decides the "Mode" based on the user's natural language.
        intent_check = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Classify this into: CRYPTO_AUDIT, BIZ_STRATEGY, or FOUNDER_PREP. Return only the word."},
                {"role": "user", "content": user_text}
            ]
        )
        intent = intent_check.choices[0].message.content.strip()

        # STEP 3: ROLE-PLAYING THE OPERATOR
        if "BIZ_STRATEGY" in intent:
            role = "You are a brutal Business Consultant. Find the flaws in this business idea. Be ruthless. Plain text, no stars."
        elif "CRYPTO_AUDIT" in intent:
            role = "You are a cynical crypto forensic auditor. Audit this for rugs, avoided questions in AMAs, and scams. Plain text, no stars."
        elif "FOUNDER_PREP" in intent:
            role = "You are a ruthless Tier-1 VC Shark. Grill the user on their project. Ask 3 lethal questions. Plain text, no stars."
        else:
            role = "You are an elite Strategic Partner. Give a blunt, operator-level response. Plain text, no stars."

        # STEP 4: GENERATE FINAL RESPONSE
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": role},
                {"role": "user", "content": user_text}
            ]
        )

        final_output = response.choices[0].message.content.replace("*", "")
        await status_msg.edit_text(final_output)

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Fault: {e}")

# --- AUDIO HANDLER ---
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
        messages=[{"role": "system", "content": "Analyze this transcript for crypto/business fraud. Be ruthless. No stars."},
                  {"role": "user", "content": transcript.text}]
    )
    clean_audio = audit.choices[0].message.content.replace("*", "")
    await status_msg.edit_text(f"🎙️ Transcript: {transcript.text}\n\n⚖️ Verdict: {clean_audio}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot_app = ApplicationBuilder().token(TOKEN).build()
    
    # Listen to everything
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    
    bot_app.run_polling()
