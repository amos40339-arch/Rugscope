import os, threading, time, collections
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq
from pydub import AudioSegment

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

# Simple In-Memory Storage (Last 10 messages per user)
memory = collections.defaultdict(lambda: [])

app = Flask(__name__)

@app.route('/')
def home():
    return "RugScope Master: Intelligence Persistent", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- THE OPERATOR BRAIN ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    
    if user_text.lower() in ['hello', 'hi', 'hey', 'start']:
        memory[user_id] = [] # Reset on start
        await update.message.reply_text("🎯 RugScope Active. Strategic Memory Engaged. What's the target today?")
        return

    status_msg = await update.message.reply_text("🕵️ RugScope is recalling context...")

    try:
        # Build the conversation history for the AI
        chat_history = memory[user_id]
        
        # Define the Ruthless Persona
        messages = [
            {"role": "system", "content": (
                "You are RugScope, a cynical forensic investigator and strategic partner. "
                "You have access to the recent chat history to maintain context. "
                "Be ruthless, blunt, and technical. No asterisks (*). "
                "If the user is an investor, protect them. If they are a founder, grill them. "
                "If you see a scam, execute a verbal autopsy. No fluff."
            )}
        ]
        
        # Add historical context (Last 5 exchanges)
        for entry in chat_history[-10:]:
            messages.append(entry)
            
        # Add current message
        messages.append({"role": "user", "content": user_text})

        # STEP 1: EXECUTION
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages
        )

        final_output = response.choices[0].message.content.replace("*", "")
        
        # STEP 2: SAVE TO MEMORY
        memory[user_id].append({"role": "user", "content": user_text})
        memory[user_id].append({"role": "assistant", "content": final_output})
        
        # Keep memory lean (Max 20 entries)
        if len(memory[user_id]) > 20:
            memory[user_id] = memory[user_id][-20:]

        await status_msg.edit_text(final_output)

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Memory Fault: {e}")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Audio follows the same ruthless logic but transcribes first
    status_msg = await update.message.reply_text("🎙️ Analyzing audio forensics...")
    try:
        file = await context.bot.get_file(update.message.voice.file_id)
        await file.download_to_drive("voice.ogg")
        audio = AudioSegment.from_ogg("voice.ogg")
        audio.export("voice.wav", format="wav")
        with open("voice.wav", "rb") as f:
            transcript = client.audio.transcriptions.create(file=("voice.wav", f.read()), model="whisper-large-v3")
        
        audit = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "Analyze for fraud and deception. Be ruthless. No stars."},
                      {"role": "user", "content": transcript.text}]
        )
        
        await status_msg.edit_text(f"⚖️ Forensic Verdict:\n{audit.choices[0].message.content.replace('*', '')}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Audio Audit Failed: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(2)
    bot_app = ApplicationBuilder().token(TOKEN).build()
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    bot_app.add_handler(MessageHandler(filters.VOICE, handle_audio))
    bot_app.run_polling()
