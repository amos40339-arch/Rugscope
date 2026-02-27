import os, logging, asyncio, threading, io
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

# --- 1. THE HEARTBEAT ---
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

# --- 3. EXTERNAL DATA (DEX & VOICE) ---
async def get_market_data(ca: str):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
            res = await client.get(url, timeout=10.0)
            data = res.json()
            if data.get('pairs'):
                p = data['pairs'][0]
                return {"name": p['baseToken']['name'], "liq": p.get('liquidity', {}).get('usd', 'N/A'), "price": p['priceUsd'], "chart": p['url']}
    except: return None
    return None

async def transcribe_voice(file_bytes):
    try:
        async with httpx.AsyncClient() as client:
            files = {'file': ('voice.ogg', file_bytes, 'audio/ogg')}
            res = await client.post("https://api.groq.com/openai/v1/audio/transcriptions", 
                                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                                    files=files, data={"model": "whisper-large-v3"})
            return res.json().get('text', "")
    except: return ""

# --- 4. THE COLD-LOGIC BRAIN ---
async def sentinel_brain(user_input: str, market_info=None):
    m_ctx = ""
    if market_info:
        m_ctx = f"TOKEN: {market_info['name']} | LIQUIDITY: {market_info['liq']} | PRICE: {market_info['price']}\n"

    role_prompt = (
        "You are the Sentinel Forensic Audit System. "
        "Strict Rule: Use plain text only. No stars, no asterisks, no bolding, no emojis. "
        "1. FOR CRYPTO: Give a SENTINEL SCORE (0-100) and an EXECUTION VERDICT (Buy/Avoid/Watch). "
        "2. FOR BUSINESS/LIFE: Identify logic leaks and ROI. Trash weak plans. "
        "3. FOR GREETINGS: Be cold. Ask for a CA or objective. "
        "End with: SENTINEL IS NOT A FINANCIAL ADVISOR."
    )

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                                    json={"model": "llama-3.1-70b-versatile", "temperature": 0.1,
                                          "messages": [{"role": "system", "content": role_prompt}, {"role": "user", "content": m_ctx + user_input}]})
            return res.json()['choices'][0]['message']['content']
    except: return "SYSTEM_ERROR: Neural bridge failure."

# --- 5. UNIFIED HANDLER ---
async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("SENTINEL: PROCESSING INPUT...")
    
    if update.message.voice:
        v_file = await update.message.voice.get_file()
        user_text = await transcribe_voice(await v_file.download_as_bytearray())
        if not user_text:
            return await status_msg.edit_text("SENTINEL: AUDIO_DATA_CORRUPT")
    else:
        user_text = update.message.text

    if not user_text: return

    m_info = await get_market_data(user_text.strip()) if len(user_text.strip()) > 30 else None
    analysis = await sentinel_brain(user_text, m_info)
    
    report = "SENTINEL FORENSIC ADVISORY\n\n"
    if m_info: report += f"TOKEN: {m_info['name']}\nCHART: {m_info['chart']}\n\n"
    report += f"{analysis}\n\nSTATUS: AUDIT COMPLETE"

    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=report)

# --- 6. MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("SENTINEL ONLINE. Send CA, Voice, or Strategy.")))
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, handle_all))
    app.run_polling()

if __name__ == '__main__': main()
    
