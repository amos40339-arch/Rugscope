"""
Sentinel — Cold, ruthless forensic auditor bot.
Telegram + Groq AI + DexScreener + Groq Whisper
Render-ready: Flask heartbeat in a daemon thread.
"""

import os
import re
import logging
import threading
import tempfile
from typing import Optional

import httpx
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Sentinel")

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY: str = os.environ["GROQ_API_KEY"]
PORT: int = int(os.environ.get("PORT", 8080))

GROQ_MODEL = "llama-3.1-70b-versatile"
WHISPER_MODEL = "whisper-large-v3"

# ─────────────────────────────────────────────
# FLASK HEARTBEAT (keeps Render port alive)
# ─────────────────────────────────────────────
flask_app = Flask(__name__)


@flask_app.route("/")
def health():
    return "Sentinel is operational.", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


# ─────────────────────────────────────────────
# CA DETECTION
# ─────────────────────────────────────────────
EVM_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def detect_ca(text: str) -> Optional[str]:
    """Return the CA if the message looks like a contract address, else None."""
    token = text.strip()
    if EVM_PATTERN.match(token) or SOLANA_PATTERN.match(token):
        return token
    return None


# ─────────────────────────────────────────────
# DEXSCREENER
# ─────────────────────────────────────────────
async def fetch_token_data(ca: str) -> dict:
    """
    Fetch token data from DexScreener.
    Returns a dict with name, liquidity, price, and chart URL.
    Raises on failure.
    """
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    pairs = data.get("pairs")
    if not pairs:
        raise ValueError("No pairs found for this contract address. It may be unlisted or invalid.")

    # Use the pair with the highest liquidity for signal reliability
    top_pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

    name = top_pair.get("baseToken", {}).get("name", "Unknown")
    symbol = top_pair.get("baseToken", {}).get("symbol", "???")
    price_usd = top_pair.get("priceUsd", "N/A")
    liquidity_usd = top_pair.get("liquidity", {}).get("usd", "N/A")
    fdv = top_pair.get("fdv", "N/A")
    volume_24h = top_pair.get("volume", {}).get("h24", "N/A")
    price_change_24h = top_pair.get("priceChange", {}).get("h24", "N/A")
    chart_url = top_pair.get("url", f"https://dexscreener.com/search?q={ca}")
    chain = top_pair.get("chainId", "unknown")

    return {
        "name": name,
        "symbol": symbol,
        "price_usd": price_usd,
        "liquidity_usd": liquidity_usd,
        "fdv": fdv,
        "volume_24h": volume_24h,
        "price_change_24h": price_change_24h,
        "chart_url": chart_url,
        "chain": chain,
        "ca": ca,
    }


# ─────────────────────────────────────────────
# GROQ — LLM
# ─────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

SENTINEL_SYSTEM_PROMPT = """You are Sentinel. A cold, forensic, ruthless capital auditor.
You do not encourage. You do not comfort. You identify risk, logic leaks, and execution failure points.

STRICT OUTPUT RULES:
- No asterisks. No stars. No markdown bolding. No bullet symbols that use *.
- Use plain text only. Use dashes (-) for lists if needed.
- Be blunt, direct, and calculated. Every sentence must earn its place.
- Never use filler phrases like "Great question" or "Certainly".

For CRYPTO audits, your output MUST include these exact labeled sections:
TOKEN NAME:
CONTRACT:
CHAIN:
PRICE USD:
LIQUIDITY USD:
24H VOLUME:
24H PRICE CHANGE:
CHART: [link]

SENTINEL SCORE: [0-100]
Score logic — deduct points ruthlessly for: low liquidity (<$50k = -30), no volume, anonymous team, no utility, high FDV/MC ratio, recent deployment, concentrated wallets.

RISK PROFILE: [brief forensic breakdown of capital risk]

EXECUTION VERDICT: [BUY / AVOID / WATCH]
VERDICT REASON: [one ruthless sentence explaining the verdict]

For BUSINESS or IDEA audits, your output MUST include:
AUDIT:
ROI POTENTIAL: [High/Medium/Low + why]
LEVERAGE SCORE: [0-100]
LOGIC LEAKS: [what will kill this idea]
EXECUTION PATH: [what would actually make this work, if anything]

For GREETINGS or GENERAL inputs, respond in character as Sentinel. Cold, brief, purposeful."""


def build_crypto_prompt(token: dict) -> str:
    return f"""Audit this token. No mercy.

Token Name: {token['name']} ({token['symbol']})
Contract: {token['ca']}
Chain: {token['chain']}
Price USD: {token['price_usd']}
Liquidity USD: {token['liquidity_usd']}
FDV: {token['fdv']}
24H Volume: {token['volume_24h']}
24H Price Change: {token['price_change_24h']}%
Chart: {token['chart_url']}

Produce your full forensic audit now."""


async def query_groq(prompt: str) -> str:
    """Non-blocking Groq call wrapped in a thread executor."""
    import asyncio

    loop = asyncio.get_event_loop()

    def _sync_call():
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SENTINEL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=1024,
        )
        return completion.choices[0].message.content.strip()

    result = await loop.run_in_executor(None, _sync_call)
    return result


# ─────────────────────────────────────────────
# GROQ — WHISPER TRANSCRIPTION
# ─────────────────────────────────────────────
async def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file using Groq Whisper."""
    import asyncio

    loop = asyncio.get_event_loop()

    def _sync_transcribe():
        with open(file_path, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_file,
                response_format="text",
            )
        return transcription.strip()

    result = await loop.run_in_executor(None, _sync_transcribe)
    return result


# ─────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sentinel online.\n\nSend a contract address (CA) for a crypto forensic audit.\n"
        "Send a business idea for a capital efficiency audit.\n"
        "Send a voice note. I will transcribe and audit it.\n\n"
        "There is no small talk here."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if not user_input:
        return

    logger.info(f"Text input from {update.effective_user.id}: {user_input[:80]}")
    await update.message.reply_text("Auditing. Stand by.")

    try:
        ca = detect_ca(user_input)

        if ca:
            # ── CRYPTO PATH ──
            try:
                token_data = await fetch_token_data(ca)
            except httpx.HTTPStatusError as e:
                await update.message.reply_text(
                    f"DexScreener returned HTTP {e.response.status_code}. "
                    "The contract may not exist on any tracked chain."
                )
                return
            except httpx.RequestError:
                await update.message.reply_text(
                    "DexScreener is unreachable. Network-level failure. Try again in 60 seconds."
                )
                return
            except ValueError as e:
                await update.message.reply_text(str(e))
                return

            prompt = build_crypto_prompt(token_data)
        else:
            # ── BUSINESS / GENERAL PATH ──
            prompt = user_input

        response = await query_groq(prompt)
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Unhandled error in handle_text: {e}", exc_info=True)
        await update.message.reply_text(
            "Groq AI inference failed. The model endpoint may be rate-limited or down. "
            "Your input was not processed. Retry in 30 seconds."
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Voice note from {update.effective_user.id}")
    await update.message.reply_text("Voice note received. Transcribing via Whisper.")

    try:
        voice = update.message.voice
        tg_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await tg_file.download_to_drive(tmp_path)
        logger.info(f"Voice file saved to {tmp_path}")

        transcript = await transcribe_audio(tmp_path)

        if not transcript:
            await update.message.reply_text(
                "Whisper returned an empty transcription. Audio may be silent or corrupted."
            )
            return

        await update.message.reply_text(f"Transcription:\n\n{transcript}\n\nAuditing now.")

        ca = detect_ca(transcript)
        if ca:
            try:
                token_data = await fetch_token_data(ca)
                prompt = build_crypto_prompt(token_data)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                await update.message.reply_text(f"DexScreener fetch failed: {e}")
                return
        else:
            prompt = transcript

        response = await query_groq(prompt)
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Unhandled error in handle_voice: {e}", exc_info=True)
        await update.message.reply_text(
            "Voice processing failed at the transcription or inference layer. "
            "Check Groq API key validity and Whisper model availability."
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# ─────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────
def main():
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask heartbeat running on port {PORT}")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logger.info("Sentinel is live. Polling for updates.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
