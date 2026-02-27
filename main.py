import os, logging, asyncio, threading, io
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

# --- 1. THE HEARTBEAT (Kills Render Port Errors) ---
server = Flask('')
@server.route('/')
def home(): return "SENTINEL_APEX_ONLINE"
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)
threading.Thread(target=run_flask, daemon=True).start()

# --- 2. CONFIG & TRACKING ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
user_usage = {} # Tracks 5-audit limit

# --- 3. EXTERNAL DATA (DEX & VOICE) ---
async def get_market_data(ca: str):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
            res = await client.get(url, timeout=10.0)
            data = res.json()
            if data.get('pairs'):
                p = data['pairs'][0]
                return {"name": p['baseToken']['name'], "price": p['priceUsd'], "liq": p.get('liquidity', {}).get('usd', 'N/A'), "chart": p['url']}
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

# --- 4. THE ULTIMATE BRAIN ---
async def sentinel_brain(user_input: str, user_id: int, market_info=None):
    # Paywall Logic
    count = user_usage.get(user_id, 0)
    if count >= 5:
        return "🔒 **ACCESS RESTRICTED:** Free quota exhausted. Contact @YOUR_USERNAME for a Professional License."
    user_usage[user_id] = count + 1

    m_ctx = ""
    if market_info:
        m_ctx = f"\n[LIVE DATA: {market_info['name']} | Price: {market_info['price']} | Liq: {market_info['liq']}]\n"

    role_prompt = (
        "You are Sentinel. A mix of a ruthless blockchain auditor and a cynical McKinsey consultant. "
        "Tone: Cold, blunt, elite. No emojis. "
        "Tasks: 1. If Crypto: Use Market Data to roast. Give a SENTINEL SCORE (0-100) and an EXECUTION VERDICT. "
        "2. If Business/Life/Hi: Identify logic leaks and ROI. Be a strategic partner. "
        "Ending: Always end with 'SENTINEL IS NOT A FINANCIAL ADVISOR.'"
    )

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post("https://api.groq.com/openai/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                                    json={"model": "llama3-70b-8192", "temperature": 0.3,
                                          "messages": [{"role": "system", "content": role_prompt}, {"role": "user", "content": m_ctx + user_input}]})
            return res.json()['choices'][0]['message']['content']
    except: return "🚨 **SYSTEM_HALT:** Neural-bridge failure."

# --- 5. UNIFIED HANDLER ---
async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text("📡 *Sentinel Logic Processing...*", parse_mode='Markdown')
    
    # Text or Voice?
    if update.message.voice:
        v_file = await update.message.voice.get_file()
        user_text = await transcribe_voice(await v_file.download_as_bytearray())
    else:
        user_text = update.message.text

    if not user_text:
        return await status_msg.edit_text("⚠️ No data detected.")

    # Market Data if it looks like a CA
    m_info = await get_market_data(user_text.strip()) if len(user_text.strip()) > 30 else None
    
    analysis = await sentinel_brain(user_text, user_id, m_info)
    
    # Final Output
    report = f"🛡️ **SENTINEL ADVISORY REPORT**\n"
    if m_info: report += f"💎 **TOKEN:** {m_info['name']}\n📊 **CHART:** [Click Here]({m_info['chart']})\n\n"
    report += f"{analysis}\n\n---\n*Status: AUDITED BY SENTINEL v3.5*"

    try:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, 
                                           text=report, parse_mode='Markdown', disable_web_page_preview=False)
    except:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=report)

# --- 6. MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("**SENTINEL ONLINE.** Send Text, Audio, or CA.", parse_mode='Markdown')))
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, handle_all))
    app.run_polling()

if __name__ == '__main__': main()
    
