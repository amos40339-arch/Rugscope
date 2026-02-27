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

GROQ_MODEL = "llama-3.3-70b-versatile"
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
    token = text.strip()
    if EVM_PATTERN.match(token) or SOLANA_PATTERN.match(token):
        return token
    return None


# ─────────────────────────────────────────────
# DEXSCREENER
# ─────────────────────────────────────────────
async def fetch_token_data(ca: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    pairs = data.get("pairs")
    if not pairs:
        raise ValueError("No pairs found for this contract address. It may be unlisted or invalid.")

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
# GROQ — SYSTEM PROMPT (FIX 2 + FIX 3)
# ─────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

SENTINEL_SYSTEM_PROMPT = """You are Sentinel. A cold, forensic, elite capital auditor with 20 years of experience in both TradFi and DeFi.
You operate with surgical precision. You do not encourage blindly. You do not comfort. You call what you see — good or bad.

STRICT OUTPUT RULES:
- No asterisks. No stars. No markdown bolding. No bullet symbols that use *.
- Use plain text only. Use dashes (-) for lists if needed.
- Be blunt, direct, and calculated. Every sentence must earn its place.
- Never use filler phrases like "Great question" or "Certainly" or "Of course".
- Always use elite industry terminology in your analysis. Required terms include:
  Capital Evaporation Risk, Neural Sentiment Gap, Liquidity Compression Zone,
  Asymmetric Execution Risk, Structural Alpha Decay, Holder Conviction Index,
  Velocity-to-Liquidity Ratio, Demand Absorption Rate, Exit Liquidity Concentration,
  Principal Preservation Threshold.

---

INPUT TYPE 1 — CRYPTO CONTRACT ADDRESS AUDIT:

Use this SENTINEL SCORE scale. You MUST score based on the actual liquidity and volume data provided.
Do NOT default to 40. Do NOT give the same score to every token. Read the numbers and score accordingly.

0-30: RUG ZONE — Liquidity below $10k. Capital evaporation is near-certain. Exit liquidity does not exist.
31-60: HIGH RISK — New launch or liquidity under $100k. Asymmetric execution risk is elevated. Speculation only.
61-85: SOLID — Liquidity above $100k with consistent volume. Structural alpha is present. Calculated entry possible.
86-100: ELITE — Blue chip on-chain metrics. High holder conviction index. Principal preservation threshold is favorable.

Your EXECUTION VERDICT MUST reflect the score:
- Score 0-30: VERDICT must be AVOID
- Score 31-60: VERDICT must be WATCH or AVOID depending on volume trend
- Score 61-85: VERDICT must be WATCH or BUY depending on momentum
- Score 86-100: VERDICT must be BUY

Your crypto audit output MUST follow this exact structure:

TOKEN NAME:
CONTRACT:
CHAIN:
PRICE USD:
LIQUIDITY USD:
24H VOLUME:
24H PRICE CHANGE:
CHART: [link]

SENTINEL SCORE: [0-100]

RISK PROFILE:
[Forensic breakdown using elite terminology. Assess capital evaporation risk,
velocity-to-liquidity ratio, demand absorption rate, and exit liquidity concentration.]

EXECUTION VERDICT: [BUY / WATCH / AVOID]
VERDICT REASON: [One ruthless, data-driven sentence.]

---

INPUT TYPE 2 — BUSINESS IDEA OR STRATEGY AUDIT:

Do NOT use crypto scoring metrics here. Do NOT output a Sentinel Score.
Provide a ruthless ROI and leverage analysis only.

Your business audit output MUST follow this exact structure:

AUDIT:
[Blunt assessment of the idea's core premise and market reality.]

ROI POTENTIAL: [High / Medium / Low]
[Explain why using structural and market analysis. Reference neural sentiment gap or asymmetric execution risk where relevant.]

LEVERAGE SCORE: [0-100]
[How much output does this idea produce per unit of input capital and effort.]

LOGIC LEAKS:
[What will kill this idea. Be specific.]

EXECUTION PATH:
[What would actually make this work. The minimum viable action sequence.]

---

INPUT TYPE 3 — GREETING OR GENERAL MESSAGE:

Respond in character as Sentinel. Cold, brief, and purposeful.
Introduce your two core capabilities: crypto forensic auditing and business strategy auditing.
Do not be warm. Do not be robotic. Be elite."""


def build_crypto_prompt(token: dict) -> str:
    return f"""Audit this token. Your score MUST reflect the actual liquidity and volume figures below.
If liquidity is above $100k, score above 60. If liquidity is above $500k with strong volume, score above 80.
Do not default to a middle score. Read the data and respond accordingly.

Token Name: {token['name']} ({token['symbol']})
Contract: {token['ca']}
Chain: {token['chain']}
Price USD: {token['price_usd']}
Liquidity USD: {token['liquidity_usd']}
FDV: {token['fdv']}
24H Volume: {token['volume_24h']}
24H Price Change: {token['price_change_24h']}%
Chart: {token['chart_url']}

Produce your full forensic audit now. No mercy. No defaults."""


# ─────────────────────────────────────────────
# GROQ — LLM
# ─────────────────────────────────────────────
async def query_groq(prompt: str) -> str:
    """Non-blocking Groq LLM call wrapped in executor."""
    import asyncio

    loop = asyncio.get_running_loop()

    def _sync_call():
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SENTINEL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=1200,
        )
        return completion.choices[0].message.content.strip()

    result = await loop.run_in_executor(None, _sync_call)
    return result


# ─────────────────────────────────────────────
# GROQ — WHISPER TRANSCRIPTION (FIX 1)
# ─────────────────────────────────────────────
async def transcribe_audio(file_path: str) -> str:
    """
    Transcribe audio using Groq Whisper large-v3.
    Prompt guides Whisper to recognize crypto and business terminology.
    """
    import asyncio

    loop = asyncio.get_running_loop()

    def _sync_transcribe():
        with open(file_path, "rb") as audio_file:
            transcription = groq_client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                response_format="text",
                prompt=(
                    "The audio is a forensic crypto audit or business strategy discussion. "
                    "Key terms may include: Liquidity, Contract Address, DexScreener, "
                    "Market Cap, FDV, Token, Solana, Ethereum, Base, ROI, leverage, capital risk, "
                    "wallet, rugpull, holder, volume, price change."
                ),
            )
        # Groq returns a plain string when response_format is "text"
        if isinstance(transcription, str):
            return transcription.strip()
        return transcription.text.strip()

    result = await loop.run_in_executor(None, _sync_transcribe)
    return result


# ─────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sentinel online.\n\n"
        "Send a contract address (CA) for a forensic crypto audit.\n"
        "Send a business idea for a capital efficiency and logic leak analysis.\n"
        "Send a voice note. I will transcribe and audit it.\n\n"
        "There is no small talk here. Only data."
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
            # ── BUSINESS / GREETING PATH ──
            prompt = user_input

        response = await query_groq(prompt)
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Unhandled error in handle_text: {e}", exc_info=True)
        await update.message.reply_text(f"Audit failed. Error: {str(e)}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Voice note from {update.effective_user.id}")
    await update.message.reply_text("Voice note received. Engaging Whisper transcription layer.")

    tmp_path = None

    try:
        voice = update.message.voice
        tg_file = await context.bot.get_file(voice.file_id)

        # FIX 1: Named temp file written to disk before transcription
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await tg_file.download_to_drive(tmp_path)
        logger.info(f"Voice file downloaded: {tmp_path} | Size: {os.path.getsize(tmp_path)} bytes")

        if os.path.getsize(tmp_path) == 0:
            await update.message.reply_text(
                "Audio file arrived empty. Telegram may have sent a corrupt packet. Re-record and retry."
            )
            return

        transcript = await transcribe_audio(tmp_path)
        logger.info(f"Transcript result: {transcript[:120]}")

        if not transcript:
            await update.message.reply_text(
                "Whisper returned an empty transcription. Audio may be silent or too short to process."
            )
            return

        await update.message.reply_text(f"Transcription complete:\n\n{transcript}\n\nInitiating audit.")

        # Route transcript through the same dual-path logic as text input
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
        await update.message.reply_text(f"Voice processing failed. Error: {str(e)}")

    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
                logger.info(f"Temp file cleaned: {tmp_path}")
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
