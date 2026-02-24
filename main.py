import os, threading, time, collections, re, requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq

# --- CONFIG & TOKENS ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
memory = collections.defaultdict(lambda: [])

# --- RENDER HEALTH CHECK (FLASK) ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "RugScope Master: Live", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- FORENSIC TOOLS (DEXSCREENER ENGINE) ---
def get_dex_data(address):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            pairs = data.get('pairs')
            if pairs:
                # Get the most liquid pair to avoid fake low-cap pools
                pair = sorted(pairs, key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
                
                # Extracting Identity Data
                base_token = pair.get("baseToken", {})
                return {
                    "name": base_token.get("name", "Unknown Project"),
                    "symbol": base_token.get("symbol", "UNKNOWN"),
                    "price": pair.get("priceUsd", "0"),
                    "liq": pair.get("liquidity", {}).get("usd", 0),
                    "mcap": pair.get("fdv", 0)
                }
    except Exception as e:
        print(f"Scraper Error: {e}")
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
    
    # 1. Management Commands
    if user_text.lower() in ['/start', 'hi', 'hello']:
        memory[user_id] = []
        await update.message.reply_text("🎯 RugScope Active. Paste a CA for a forensic audit or pitch me a play.")
        return

    # 2. Chain & Data Fetching
    chain = detect_chain(user_text)
    data = get_dex_data(user_text) if chain else None

    # 3. Decision Logic
    if chain and data:
        # We inject the NAME and SYMBOL into the brain
        id_str = f"TARGET: {data['name']} ({data['symbol']})"
        stats_str = f"PRICE: ${data['price']} | LIQUIDITY: ${data['liq']:,} | MCAP: ${data['mcap']:,}"
        
        system_prompt = (
            f"You are RugScope, a cynical forensic auditor. {id_str}. {stats_str}. "
            "INSTRUCTIONS: \n"
            "1. Start by identifying the coin by NAME and SYMBOL.\n"
            "2. Give a blunt BUY, SELL, or AVOID verdict.\n"
            "3. If Liquidity is less than 5% of MCAP, call it a RUG.\n"
            "4. Be aggressive and concise. No stars. No fluff."
        )
    elif chain and not data:
        system_prompt = (
            f"The user sent a {chain} address, but DexScreener shows NO data. "
            "Verdict: AVOID. Tell the user this is either a stealth rug or a ghost coin. "
            "Do not be polite. No stars."
        )
    else:
        system_prompt = "You are RugScope, an operator's mentor. Roast lazy thinking. Give practical business/trading advice. No stars."

    # 4. API Execution
    try:
        messages = [{"role": "system", "content": system_prompt}]
        for entry in memory[user_id][-4:]: messages.append(entry)
        messages.append({"role": "user", "content": user_text})

        chat = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.1 # Absolute precision, no "creative" guessing
        )
        
        response = chat.choices[0].message.content.replace("*", "")
        memory[user_id].append({"role": "user", "content": user_text})
        memory[user_id].append({"role": "assistant", "content": response})
        
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Brain Fault: {str(e)}")

# --- MAIN RUNTIME ---
if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    
    if not TOKEN:
        print("CRITICAL: TELEGRAM_TOKEN missing.")
        exit(1)

    try:
        app_bot = ApplicationBuilder().token(TOKEN).build()
        app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("--- RUGSCOPE OPERATIONAL: IDENTITY MODE ---")
        app_bot.run_polling(drop_pending_updates=True)
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        
