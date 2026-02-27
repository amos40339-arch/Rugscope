import os
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import httpx  # Using httpx for async API calls

# 1. LOGGING SETUP (Essential for 24/7 Uptime)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. CONFIGURATION
TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# 3. THE "SENTINEL" BRAIN (Adversarial Logic)
async def analyze_contract(contract_address: str):
    try:
        async with httpx.AsyncClient() as client:
            # We are calling Groq (Llama-3) to run the "Forensic Audit"
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama3-70b-8192",
                    "messages": [
                        {"role": "system", "content": "You are the Sentinel Forensic Logic. Cynical auditor. Identify Exit-Deltas and Liquidity-Voids. Be blunt. If it looks like a rug, say REJECTED."},
                        {"role": "user", "content": f"Run a forensic audit on this CA: {contract_address}"}
                    ]
                },
                timeout=10.0
            )
            data = response.json()
            return data['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"Brain Error: {e}")
        return "🚨 **FORENSIC TIMEOUT:** Unable to reach Neural-Bridge. Chain congestion detected."

# 4. HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "**[SENTINEL FORENSIC SUITE v2.8.4]**\n"
        "**Neural-Logic Status:** `OPERATIONAL`\n\n"
        "I provide **Forensic Truth.** I do not provide financial advice.\n"
        "Input a Contract Address to execute an Adversarial Audit."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    # Simple check for CA length (approx 44 for Sol, 42 for ETH)
    if len(user_text) > 30:
        processing_msg = await update.message.reply_text("🔍 *Executing Neural-Logic Audit...*", parse_mode='Markdown')
        analysis = await analyze_contract(user_text)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_msg.message_id,
            text=analysis,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("⚠️ Invalid CA. Provide a full Solana/Base/ETH contract address.")

# 5. GLOBAL ERROR HANDLER (The "Uncrashable" Shield)
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # This prevents the bot from stopping when an error occurs
    pass

# 6. MAIN EXECUTION
def main():
    if not TOKEN:
        print("ERROR: TELEGRAM_TOKEN not found in environment.")
        return

    # Build the application
    app = ApplicationBuilder().token(TOKEN).build()

    # Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # Add Global Error Shield
    app.add_error_handler(error_handler)

    print("Sentinel Forensic Suite is LIVE.")
    app.run_polling()

if __name__ == '__main__':
    main()
    
