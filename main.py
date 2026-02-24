import os, threading, time, collections, re, requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq
from pydub import AudioSegment

# --- CONFIG ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

memory = collections.defaultdict(lambda: [])

app = Flask(__name__)

@app.route('/')
def home():
    return "RugScope Master: Live Intelligence Active", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- LIVE DATA: DEXSCREENER INTEGRATION ---
def get_dex_data(address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get('pairs'):
                # Take the pair with highest liquidity
                pair = sorted(data['pairs'], key=lambda x: x.get('liquidity', {}).get('usd', 0), reverse=True)[0]
                return {
                    "price": pair.get("priceUsd"),
                    "liquidity": pair.get("liquidity", {}).get("usd"),
                    "vol24h": pair.get("volume", {}).get("h24"),
                    "mcap": pair.get("fdv"),
                    "pair_url": pair.get("url")
                }
    except:
        return None
    return None

# --- CHAIN DETECTION ---
def detect_chain(text):
    if re.search(r'0x[a-fA-F0-9]{40}', text): return "EVM"
    if re.search(r'[1-9A-HJ-NP-Za-km-z]{32,44}', text): return "SOLANA"
    if re.search(r'(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,59}', text): return "BITCOIN"
    return None

# --- THE OPERATOR BRAIN ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    
    if user_text.lower() in ['hello', 'hi', 'hey', 'start']:
        memory[user_id] = []
        await update.message.reply_text("🎯 RugScope Master Active. I see live chain data now. Send a CA or a pitch.")
        return

    status_msg = await update.message.reply_text("🕵️ RugScope is calculating...")

    try:
        chain = detect_chain(user_text)
        live_stats = ""
        
        if chain:
            # TRY TO FETCH LIVE DATA
            data = get_dex_data(user_text)
            if data:
                live_stats = (
                    f"\n[LIVE DATA DETECTED]\n"
                    f"Price: ${data['price']}\nLiq: ${data['liquidity']:,}\n"
                    f"Vol 24h: ${data['vol24h']:,}\nMCap: ${data['mcap']:,}\n"
                )
            
            system_prompt = (
                f"You are RugScope, a ruthless forensic investigator. The user provided a {chain} address. "
                f"Live Stats Provided: {live_stats if live_stats else 'None found'}. "
                "GIVE A VERDICT: BUY, SELL, or AVOID. Analyze the risk based on these numbers. "
                "If liquidity is low vs market cap, call it a rug. Be blunt. No stars."
            )
        else:
            system_prompt = (
                "You are RugScope, an elite strategic partner. Use history for context. "
                "If they are an investor, protect them. If a founder, grill them. No stars."
            )

        # Build Conversation
        messages = [{"role": "system", "content": system_prompt}]
        for entry in memory[user_id][-8:]:
            messages.append(entry)
        messages.append({"role": "user", "content": user_text})

        response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages)
        final_output = response.choices[0].message.content.replace("*", "")
        
        # Save to memory
        memory[user_id].append({"role": "user", "content": user_text})
        memory[user_id].append({"role": "assistant", "content": final_output})

        await status_msg.edit_text(final_output)

    except Exception as e:
        await status_msg.edit_text(f"⚠️ Operational Fault: {e}")

# (Existing handle_audio logic remains integrated below handle_message)
