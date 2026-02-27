import os, logging, asyncio, threading, io
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

# --- 1. HEARTBEAT (Kills Render Port Errors) ---
server = Flask('')
@server.route('/')
def home(): return "SENTINEL_APEX_ONLINE"
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)
threading.Thread(target=run_flask, daemon=True).start()

# --- 2. CONFIG & LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
user_usage = {}

# --- 3. EXTERNAL DATA (DEX & VOICE) ---
async def get_market_data(ca: str):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
            res = await client.get(url, timeout=10.0)
            data = res.json()
            if data.get('pairs'):
                p = data['pairs'][0]
                return {
                    "name": p['baseToken']['name'], 
                    "price": p['priceUsd'], 
                    "liq": p.get('liquidity', {}).get('usd', 'N/A'), 
                    "chart": p['url']
                }
    except Exception as e:
        logger.error(f"DEX Error: {e}")
        return None
    return None

async def transcribe_voice(file_bytes):
    try:
        async with httpx.AsyncClient() as client:
            files = {'file': ('voice.ogg', file_bytes, 'audio/ogg')}
            res = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions", 
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files=files, 
                data={"model": "whisper-large-v3"}
            )
            return res.json().get('text', "")
    except Exception as e:
        logger.error(f"Voice Transcribe Error: {e}")
        return ""

# --- 4. THE ULTIMATE BRAIN ---
async def sentinel_brain(user_input: str, user_id: int, market_info=None):
    # Paywall Logic
    count = user_usage.get(user_id, 0)
    if count >= 10: # Increased to 10 for your testing
        return "ACCESS RESTRICTED: Free quota exhausted. Contact @YOUR_USERNAME for a Professional License."
    user_usage[user_id] = count + 1

    m_ctx = ""
    if market_info:
        m_ctx = f"TOKEN_NAME: {market_info['name']} | PRICE: {market_info['price']} | LIQUIDITY: {market_info['liq']}\n"

    role_prompt = (
        "You are Sentinel. A mix of a ruthless blockchain auditor and a cynical strategy consultant. "
        "Tone: Cold, blunt, elite. No emojis. Do not use asterisks or stars for bolding. "
        "1. If Crypto: Use Market Data to roast. Give a SENTINEL SCORE (0-100) and an EXECUTION VERDICT. "
        "2. If Business/Life: Identify logic leaks and ROI. "
        "Ending: Always end with SENTINEL IS NOT A FINANCIAL ADVISOR."
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.1-70b-versatile",
                    "messages": [
                        {"role": "system", "content": role_prompt},
                        {"role": "user", "content": m_ctx + user_input}
                    ],
                    "temperature": 0.2
                },
                timeout=20.0
            )
            
            if response.status_code != 200:
                logger.error(f"Groq API returned {response.status_code}: {response.text}")
                return f"SYSTEM_GAP: AI Provider Error {response.status_code}. Check API Key."

            return response.json()['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"Brain Failure: {e}")
        return f"NEURAL_FAIL: {str(e)[:50]}"

# --- 5. UNIFIED HANDLER ---
async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Removed stars
