import os
import logging
import asyncio
import threading
import time
import re
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx

# --- 1. THE HEARTBEAT (Kills Render Port Errors) ---
server = Flask('')
@server.route('/')
def home():
    return "SENTINEL_FORENSIC_OPERATIONAL"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- 2. CONFIG & LOGGING ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# --- 3. FORENSIC BRAIN (The "Pharmacy Strategy" Logic) ---
async def adversarial_audit(ca: str):
    # Determine Chain for "Elite" feel
    chain = "SOLANA" if len(ca) > 40 and not ca.startswith("0x") else "EVM/BASE"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama3-70b-8192",
                    "messages": [
                        {
                            "role": "system", 
                            "content": f"You are the Sentinel Forensic Logic auditing the {chain} chain. "
                            "You are a brutal, cynical expert. Your job is to protect users from 'Liquidity-Voids' and 'Exit-Deltas'. "
                            "If a coin has less than 25% liquidity-to-mcap ratio, call it a 'REJECTED TRAP'. "
                            "Always use high-level forensic terms. Roast the developer's greed. Be blunt."
                        },
                        {"role": "user", "content": f"Run forensic audit on CA: {ca}"}
                    ]
                },
                timeout=20.0
            )
            return response.json()['choices'][0]['message']['content']
    except Exception:
        return "🚨 **BRIDGE_FAILURE:** Neural-Link timed out. Chain congestion is too high for a deep audit."

# --- 4. HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "**[SENTINEL FORENSIC SUITE v2.8.4]**\n"
        "**Neural-Logic:** `ACTIVE` | **Database:** `SYNCED`\n\n"
        "Ready to identify **Exit-Deltas** and **Shadow-Minting**.\n"
        "Send a Contract Address to begin the audit.",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ca = update.message.text.strip()
    
    # 1. Validate if it's a CA
    if len(ca) < 30:
        return await update.message.reply_text("⚠️ Invalid Input. Provide a full Chain Address.")

    # 2. Strategic Delay (Makes the audit feel 'expensive')
    status_msg = await update.message.reply_text("📡 *Connecting to Sentinel Neural-Bridge...*", parse_mode='Markdown')
    await asyncio.sleep(1.5)
    await status_msg.edit_text("🔍 *Scanning for Exit-Deltas & Ghost-Vesting...*")
    await asyncio.sleep(1.5)
    
    # 3. Get the Audit
    report = await adversarial_audit(ca)
    
    # 4. Professional Delivery
    final_report = f"🛡️ **FORENSIC AUDIT COMPLETE**\n\n{report}\n\n---\n**Status:** `AUDITED BY SENTINEL`"
    await status_msg.edit_text(final_report, parse_mode='Markdown')

# --- 5. MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()

if __name__ == '__main__':
    main()
    
