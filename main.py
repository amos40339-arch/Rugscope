import os
import logging
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

# --- 1. THE HEARTBEAT (Kills Render Port Errors) ---
server = Flask('')
@server.route('/')
def home(): return "SENTINEL_OPERATIONAL"
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)
threading.Thread(target=run_flask, daemon=True).start()

# --- 2. CONFIG ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# --- 3. THE "ELITE OPERATOR" BRAIN ---
async def sentinel_brain(user_input: str):
    # Detect if it's a CA or general talk
    is_ca = len(user_input) > 30 and (user_input.startswith("0x") or not " " in user_input)
    
    role_prompt = (
        "You are Sentinel. You are a mix of a ruthless blockchain auditor and a high-level McKinsey strategy consultant. "
        "Your tone is blunt, cold, and efficient. "
        "1. If the user sends a crypto CA: Audit it for 'Exit-Deltas' and 'Liquidity-Voids'. "
        "2. If the user sends a business idea or general talk: Analyze it for ROI, leverage, and 'Lazy Thinking'. "
        "Trash weak ideas. Be a strategic partner. Do not be polite. Never use emojis like 😊 or ✨."
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama3-70b-8192",
                    "messages": [
                        {"role": "system", "content": role_prompt},
                        {"role": "user", "content": user_input}
                    ]
                },
                timeout=20.0
            )
            return response.json()['choices'][0]['message']['content']
    except Exception:
        return "🚨 **SYSTEM_HALT:** Neural-link interrupted."

# --- 4. HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "**[SENTINEL SYSTEM v2.9.0]**\n"
        "**Status:** `ADAPTIVE_LOGIC_ACTIVE`\n\n"
        "Send a **Contract Address** for forensic audit, or a **Business Strategy** for ruthless optimization.",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    
    # Visual feedback for the 'Expensive' feel
    status_msg = await update.message.reply_text("📡 *Processing through Sentinel Logic...*", parse_mode='Markdown')
    
    analysis = await sentinel_brain(user_text)
    
    await status_msg.edit_text(f"🛡️ **SENTINEL ADVISORY:**\n\n{analysis}", parse_mode='Markdown')

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This uses Groq Whisper to handle audio if you want to flex that feature
    voice_file = await update.message.voice.get_file()
    # (Transcription logic goes here - for now, we focus on the text brain)
    await update.message.reply_text("🎙️ Voice parsing requires Whisper-3 Bridge. Send text for instant audit.")

# --- 5. MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.run_polling()

if __name__ == '__main__':
    main()
