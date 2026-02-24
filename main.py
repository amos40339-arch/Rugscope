import os, threading, time, collections, re, requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
memory = collections.defaultdict(lambda: [])

app = Flask(__name__)
@app.route('/')
def health(): return "RugScope Live", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- THE DATA ENGINE ---
def get_dex_data(address):
    try:
        # Hardened headers to look like a real browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        response = requests.get(url, headers=headers, timeout=8)
        
        if response.status_code == 200:
            res_data = response.json()
            pairs = res_data.get('pairs')
            if pairs and len(pairs) > 0:
                # Get the most liquid pair
                p = sorted(pairs, key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
                base = p.get("baseToken", {})
                return {
                    "name": base.get("name", "Unknown"),
                    "symbol": base.get("symbol", "???"),
                    "price": p.get("priceUsd", "0"),
                    "liq": p.get("liquidity", {}).get("usd", 0),
                    "mcap": p.get("fdv", 0)
                }
    except Exception as e:
        print(f"Fetch Error: {e}")
    return None

def detect_chain(text):
    if re.search(r'0x[a-fA-F0-9]{40}', text): return "EVM"
    if re.search(r'[1-9A-HJ-NP-Za-km-z]{32,44}', text): return "SOLANA"
    return None

# --- THE BRAIN ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    
    chain = detect_chain(user_text)
    data = get_dex_data(user_text) if chain else None

    # SYSTEM PROMPT INJECTION
    if chain and data:
        # DATA FOUND: Force the AI to use it
        context_data = f"COIN: {data['name']} ({data['symbol']}) | MCAP: ${data['mcap']:,} | LIQ: ${data['liq']:,} | PRICE: ${data['price']}"
        system_msg = (
            f"You are RugScope, a ruthless auditor. TARGET IDENTIFIED: {context_data}. "
            "1. Lead with the Name and Symbol. "
            "2. Give a blunt BUY/SELL/AVOID verdict. "
            "3. If Liq < 10% of MCAP, call it a dangerous RUG. "
            "Be aggressive. No fluff. No stars."
        )
    elif chain and not data:
        # CA SENT BUT NO DATA: No guessing allowed
        system_msg = "The user sent a CA but DexScreener has no data. This is a GHOST project or a fresh scam. Tell them to AVOID and don't make up numbers."
    else:
        # NORMAL CHAT
        system_msg = "You are RugScope, a cynical mentor. Roast lazy thinking. Be blunt. No stars."

    try:
        msgs = [{"role": "system", "content": system_msg}]
        for m in memory[user_id][-4:]: msgs.append(m)
        msgs.append({"role": "user", "content": user_text})

        res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=msgs, temperature=0.1)
        final_text = res.choices[0].message.content.replace("*", "")
        
        memory[user_id].append({"role": "user", "content": user_text})
        memory[user_id].append({"role": "assistant", "content": final_text})
        await update.message.reply_text(final_text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    bot = ApplicationBuilder().token(TOKEN).build()
    bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("--- RUGSCOPE 2.2 ONLINE ---")
    bot.run_polling(drop_pending_updates=True)
