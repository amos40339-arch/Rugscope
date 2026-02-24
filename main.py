import os, threading, time, collections, re, requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq

# --- CONFIG & TOKENS ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Create the Groq client only if key is present to avoid crash
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)
else:
    client = None

memory = collections.defaultdict(lambda: [])

# --- RENDER HEALTH CHECK (FLASK) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "RugScope Master: Live", 200

def run_flask():
    # Render uses port 10000 by default, but we pull from ENV to be safe
    port = int(os.environ.get("PORT", 10000))
    print(f"Flask trying to bind to port {port}...")
    app.run(host='0.0.0.0', port=port)

# --- FORENSIC TOOLS ---
def get_dex_data(address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('pairs'):
                pair = sorted(data['pairs'], key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
                return {
                    "price": pair.get("priceUsd"),
                    "liq": pair.get("liquidity", {}).get("usd"),
                    "mcap": pair.get("fdv")
                }
    except: return None
    return None

def detect_chain(text):
    if re.search(r'0x[a-fA-F0-9]{40}', text): return "EVM"
    if re.search(r'[1-9A-HJ-NP-Za-km-z]{32,44}', text): return "SOLANA"
    return None

# --- BOT LOGIC ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    
    if user_text.lower() in ['/start', 'hi', 'hello']:
        memory[user_id] = []
        await update.message.reply_text("🎯 RugScope Active. Strategic Partner Mode Engaged. Target?")
        return

    chain = detect_chain(user_text)
    live_stats = ""
    
    if chain:
        data = get_dex_data(user_text)
        if data:
            live_stats = f"\n[LIVE] Price: ${data['price']} | Liq: ${data['liq']:,} | MC: ${data['mcap']:,}\n"
        
        system_prompt = (
            f"You are RugScope, a ruthless forensic auditor. Target: {chain} address. "
            f"Data: {live_stats if live_stats else 'No live liquidity found'}. "
            "Verdict: BUY, SELL, or AVOID. Be blunt. No stars. No fluff."
        )
    else:
        system_prompt = (
            "You are RugScope, a cynical strategic partner. Use history for context. "
            "If user is an investor, protect them. If founder, grill them. Be honest. No stars."
        )

    try:
        messages = [{"role": "system", "content": system_prompt}]
        for entry in memory[user_id][-6:]: messages.append(entry)
        messages.append({"role": "user", "content": user_text})

        chat_completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages
        )
        
        response_text = chat_completion.choices[0].message.content.replace("*", "")
        memory[user_id].append({"role": "user", "content": user_text})
        memory[user_id].append({"role": "assistant", "content": response_text})
        
        await update.message.reply_text(response_text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Brain Fault: {str(e)}")

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # 1. Immediate Flask Startup to satisfy Render's port checker
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Short sleep to let Flask bind before Telegram starts
    time.sleep(2)
    
    # 3. Start Telegram Bot
    if not TOKEN or not GROQ_API_KEY:
        print("CRITICAL ERROR: Missing Environment Variables!")
        exit(1)

    try:
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        
        print("--- RUGSCOPE OPERATIONAL ---")
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"CRITICAL SHUTDOWN: {e}")
    
