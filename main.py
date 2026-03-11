"""
Sentinel — Elite forensic auditor bot. Full natural language build.
No slash commands. Sentinel reads intent from plain English.
Telegram + Groq AI + DexScreener + Groq Whisper + PyMuPDF
Features: NL Intent Detection, Rate Limiting, Conversation Memory,
          Portfolio Tracker, Price Alerts, Whitepaper Forensics,
          AMA/URL Intelligence
Render-ready: Flask heartbeat in a daemon thread.
NOTE: All state is in-memory. Resets on server restart.
"""

import os
import re
import time
import logging
import threading
import tempfile
from typing import Optional

import httpx
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
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
RATE_LIMIT_SECONDS = 15
MAX_MEMORY_EXCHANGES = 3
MAX_PORTFOLIO_SIZE = 10
MAX_PDF_CHARS = 6000
MAX_URL_CHARS = 5000

# ─────────────────────────────────────────────
# FLASK HEARTBEAT
# ─────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Sentinel is operational.", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

# ─────────────────────────────────────────────
# IN-MEMORY STATE
# ─────────────────────────────────────────────
user_cooldowns: dict = {}
user_memory: dict = {}
user_portfolios: dict = {}
price_alerts: list = []

# ─────────────────────────────────────────────
# RATE LIMITING
# ─────────────────────────────────────────────
def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = user_cooldowns.get(user_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return True
    user_cooldowns[user_id] = now
    return False

# ─────────────────────────────────────────────
# CONVERSATION MEMORY
# ─────────────────────────────────────────────
def get_user_memory(user_id: int) -> list:
    return user_memory.get(user_id, [])

def update_user_memory(user_id: int, user_msg: str, assistant_msg: str):
    history = user_memory.get(user_id, [])
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": assistant_msg})
    if len(history) > MAX_MEMORY_EXCHANGES * 2:
        history = history[-(MAX_MEMORY_EXCHANGES * 2):]
    user_memory[user_id] = history

def clear_user_memory(user_id: int):
    user_memory.pop(user_id, None)

# ─────────────────────────────────────────────
# PATTERN DETECTION
# ─────────────────────────────────────────────
EVM_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}")
SOLANA_PATTERN = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{43,44}\b")
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
PRICE_PATTERN = re.compile(r"\$?([\d]+\.?[\d]*)", re.IGNORECASE)


def extract_ca(text: str) -> Optional[str]:
    evm = EVM_PATTERN.search(text)
    if evm:
        return evm.group(0)
    sol = SOLANA_PATTERN.search(text)
    if sol:
        return sol.group(0)
    return None


def extract_url(text: str) -> Optional[str]:
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def extract_price(text: str) -> Optional[float]:
    match = PRICE_PATTERN.search(text)
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


# ─────────────────────────────────────────────
# INTENT DETECTOR
#
# FIXED: All .{0,N} limits audited and increased to
# {0,120} wherever a full Solana CA (44 chars) plus
# surrounding words could sit between two keywords.
# This prevents long CAs from breaking pattern matches.
#
# Patterns checked BEFORE CA/URL hard detection so
# "alert me when [CA]..." sets an alert instead of
# triggering a crypto audit.
# ─────────────────────────────────────────────
INTENT_PATTERNS = {
    # FIXED: {0,40} → {0,120} — CA can sit between action word and "token/this/it"
    "portfolio_add": re.compile(
        r"\b(add|track|watch|monitor|save|include)\b.{0,120}\b(token|coin|ca|contract|this|it)\b"
        r"|\b(add|track|watch|monitor)\b.{0,120}(portfolio|watchlist|list)\b"
        r"|\b(put|throw|drop)\b.{0,120}(portfolio|watchlist)\b",
        re.IGNORECASE
    ),
    # FIXED: {0,40} → {0,120} — CA can sit between "remove" and "token/contract/this"
    "portfolio_remove": re.compile(
        r"\b(remove|delete|drop|untrack|take off|get rid of)\b.{0,120}"
        r"\b(token|coin|ca|contract|this|it|portfolio|watchlist)\b",
        re.IGNORECASE
    ),
    # No CA expected mid-sentence here — kept as-is, safe
    "portfolio_scan": re.compile(
        r"\b(scan|audit|check|analyze|review)\b.{0,30}"
        r"\b(portfolio|watchlist|all|my tokens|my coins|tracked)\b"
        r"|\b(portfolio|watchlist)\b.{0,20}\b(scan|audit|check|analyze)\b"
        r"|\bhow (are|is) my (tokens|coins|portfolio)\b"
        r"|\bcheck (everything|all of them|my stuff)\b",
        re.IGNORECASE
    ),
    # No CA expected — kept as-is, safe
    "portfolio_list": re.compile(
        r"\b(show|list|display|what('s| is| are)|view|see)\b.{0,30}"
        r"\b(portfolio|watchlist|tracked|my tokens|my coins)\b"
        r"|\bwhat do i (have|own|track)\b"
        r"|\bmy portfolio\b",
        re.IGNORECASE
    ),
    # FIXED: {0,50} → {0,120} — CA sits between "alert/notify" and "when/above/below"
    # This was the confirmed bug that caused "alert me when [CA] goes above X"
    # to fall through to crypto_audit instead of setting an alert
    "alert_set": re.compile(
        r"\b(alert|notify|tell|ping|warn|let me know|hit me)\b.{0,120}"
        r"\b(when|if|once|hits?|reaches?|goes?|drops?|falls?|above|below)\b"
        r"|\bset.{0,120}(alert|notification|alarm)\b"
        r"|\b(price|it).{0,120}(hits?|reaches?|goes? (above|below|to|over|under))\b",
        re.IGNORECASE
    ),
    # No CA expected — kept as-is, safe
    "alert_list": re.compile(
        r"\b(show|list|display|what|view|see)\b.{0,30}\b(alerts?|notifications?)\b"
        r"|\bmy alerts?\b"
        r"|\bactive alerts?\b",
        re.IGNORECASE
    ),
    # FIXED: {0,30} → {0,120} — CA can sit between "cancel" and "alert"
    "alert_cancel": re.compile(
        r"\b(cancel|remove|delete|stop|clear|turn off)\b.{0,120}\b(alert|notification)\b",
        re.IGNORECASE
    ),
    # No CA expected — kept as-is, safe
    "clear_memory": re.compile(
        r"\b(forget|clear|reset|wipe|delete|erase)\b.{0,30}"
        r"\b(memory|history|context|conversation|chat|everything|what we|what you)\b"
        r"|\bstart (over|fresh|again)\b"
        r"|\bnew conversation\b",
        re.IGNORECASE
    ),
    # Exact match only — no CA expected, safe
    "help": re.compile(
        r"^(help|what can you do|capabilities|commands|how does this work|"
        r"what do you do|who are you|sentinel|what are you)\??$",
        re.IGNORECASE
    ),
}


def detect_intent(text: str) -> str:
    """
    NL intent patterns checked FIRST.
    CA and URL are fallback signals — only fire when
    no action words are present (e.g. a raw CA paste).
    """
    clean = text.strip()

    # NL intents first — catches "alert me when [CA]..." correctly
    for intent, pattern in INTENT_PATTERNS.items():
        if pattern.search(clean):
            return intent

    # Hard signal fallback — raw CA or URL with no action words
    if extract_ca(clean):
        return "crypto_audit"
    if extract_url(clean):
        return "url_audit"

    return "general"


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

    return {
        "name": top_pair.get("baseToken", {}).get("name", "Unknown"),
        "symbol": top_pair.get("baseToken", {}).get("symbol", "???"),
        "price_usd": top_pair.get("priceUsd", "N/A"),
        "liquidity_usd": top_pair.get("liquidity", {}).get("usd", "N/A"),
        "fdv": top_pair.get("fdv", "N/A"),
        "volume_24h": top_pair.get("volume", {}).get("h24", "N/A"),
        "price_change_24h": top_pair.get("priceChange", {}).get("h24", "N/A"),
        "chart_url": top_pair.get("url", f"https://dexscreener.com/search?q={ca}"),
        "chain": top_pair.get("chainId", "unknown"),
        "ca": ca,
    }


# ─────────────────────────────────────────────
# PDF EXTRACTION
# ─────────────────────────────────────────────
def extract_pdf_text(file_path: str) -> str:
    doc = fitz.open(file_path)
    text_parts = [page.get_text() for page in doc]
    doc.close()
    full_text = "\n".join(text_parts).strip()
    if len(full_text) > MAX_PDF_CHARS:
        full_text = full_text[:MAX_PDF_CHARS] + "\n\n[TRUNCATED — First portion analyzed]"
    return full_text


# ─────────────────────────────────────────────
# URL SCRAPER
# ─────────────────────────────────────────────
async def fetch_url_content(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SentinelBot/1.0)"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    lines = [l.strip() for l in soup.get_text(separator="\n").splitlines() if l.strip()]
    clean = "\n".join(lines)
    if len(clean) > MAX_URL_CHARS:
        clean = clean[:MAX_URL_CHARS] + "\n\n[TRUNCATED]"
    return clean


# ─────────────────────────────────────────────
# GROQ CLIENT + SYSTEM PROMPT
# ─────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

SENTINEL_SYSTEM_PROMPT = """You are Sentinel. A cold, forensic, elite capital auditor with 20 years of experience in TradFi, DeFi, and venture intelligence.
You operate with surgical precision. You call what you see — good or bad — without bias.

STRICT OUTPUT RULES:
- No asterisks. No stars. No markdown bolding. No bullet symbols that use *.
- Use plain text only. Use dashes (-) for lists if needed.
- Be blunt, direct, and calculated. Every sentence must earn its place.
- Never use filler phrases like "Great question", "Certainly", or "Of course".
- Always use elite industry terminology. Required terms include:
  Capital Evaporation Risk, Neural Sentiment Gap, Liquidity Compression Zone,
  Asymmetric Execution Risk, Structural Alpha Decay, Holder Conviction Index,
  Velocity-to-Liquidity Ratio, Demand Absorption Rate, Exit Liquidity Concentration,
  Principal Preservation Threshold, Narrative Integrity Index, Founder Credibility Vector.

---

INPUT TYPE 1 — CRYPTO CONTRACT ADDRESS AUDIT:

SENTINEL SCORE SCALE — Score based strictly on actual data. Do NOT default to 40.
0-30:  RUG ZONE — Liquidity below $10k. Capital evaporation near-certain.
31-60: HIGH RISK — Liquidity under $100k. Asymmetric execution risk elevated.
61-85: SOLID — Liquidity above $100k, consistent volume. Structural alpha present.
86-100: ELITE — Blue chip metrics. High holder conviction index.

VERDICT rules:
- 0-30:  AVOID
- 31-60: WATCH or AVOID (based on volume trend)
- 61-85: WATCH or BUY (based on momentum)
- 86-100: BUY

Output format:
TOKEN NAME:
CONTRACT:
CHAIN:
PRICE USD:
LIQUIDITY USD:
24H VOLUME:
24H PRICE CHANGE:
CHART: [link]

SENTINEL SCORE: [0-100]
RISK PROFILE: [Forensic breakdown using elite terminology]
EXECUTION VERDICT: [BUY / WATCH / AVOID]
VERDICT REASON: [One ruthless, data-driven sentence]

---

INPUT TYPE 2 — BUSINESS IDEA OR STRATEGY AUDIT:

Do NOT use crypto scoring metrics. Do NOT output a Sentinel Score.

Output format:
AUDIT: [Core premise assessment]
ROI POTENTIAL: [High/Medium/Low + why]
LEVERAGE SCORE: [0-100]
LOGIC LEAKS: [Specific failure vectors]
EXECUTION PATH: [Minimum viable action sequence]

---

INPUT TYPE 3 — WHITEPAPER FORENSIC AUDIT:

Analyze for authenticity, technical credibility, and founder integrity.
Flag: vague tokenomics, plagiarized language, unrealistic ROI promises, missing technical specs,
anonymous team with no track record, no audit history, generic roadmaps without dates.

Output format:
WHITEPAPER FORENSIC REPORT

NARRATIVE INTEGRITY INDEX: [0-100]
- 0-40: Fabricated or heavily plagiarized
- 41-70: Suspicious
- 71-100: Credible

TECHNICAL CREDIBILITY: [High/Medium/Low]
TOKENOMICS AUDIT: [Supply, distribution, vesting assessment]
FOUNDER CREDIBILITY VECTOR: [Team identification and credential analysis]
RED FLAGS DETECTED: [Every suspicious element]
WHITEPAPER VERDICT: [LEGITIMATE / SUSPICIOUS / FABRICATED]
VERDICT REASON: [One forensic sentence]

---

INPUT TYPE 4 — AMA / URL / ARTICLE INTELLIGENCE AUDIT:

Analyze for founder credibility, narrative consistency, and capital risk signals.
Look for: deflection patterns, vague answers, contradictions, unrealistic promises, manipulation.

Output format:
INTELLIGENCE REPORT

SOURCE ANALYSIS: [Content type and producer]
NARRATIVE CONSISTENCY SCORE: [0-100]
CREDIBILITY SIGNALS DETECTED: [Positive legitimacy indicators]
RED FLAGS DETECTED: [Deception patterns and manipulation tactics]
NEURAL SENTIMENT GAP: [Delta between what is said and what data suggests]
INTELLIGENCE VERDICT: [CREDIBLE / SUSPICIOUS / FABRICATED]
VERDICT REASON: [One ruthless forensic sentence]

---

INPUT TYPE 5 — GREETING OR GENERAL MESSAGE:

Respond as Sentinel. Cold, brief, elite.
Tell the user what you can do in plain terms — no slash commands, no syntax.
Just explain: send a CA, send a business idea, send a whitepaper PDF, send a URL,
send a voice note, ask to track tokens, set price alerts, or scan their portfolio."""


# ─────────────────────────────────────────────
# PROMPT BUILDERS
# ─────────────────────────────────────────────
def build_crypto_prompt(token: dict) -> str:
    return f"""Audit this token. Score MUST reflect actual data.
Liquidity > $100k = score above 60. Liquidity > $500k with strong volume = score above 80.

Token Name: {token['name']} ({token['symbol']})
Contract: {token['ca']}
Chain: {token['chain']}
Price USD: {token['price_usd']}
Liquidity USD: {token['liquidity_usd']}
FDV: {token['fdv']}
24H Volume: {token['volume_24h']}
24H Price Change: {token['price_change_24h']}%
Chart: {token['chart_url']}

Produce full forensic audit now."""


def build_whitepaper_prompt(text: str, filename: str) -> str:
    return f"""Perform a full whitepaper forensic audit.
Filename: {filename}

DOCUMENT CONTENT:
{text}

Identify every red flag. Issue a verdict."""


def build_ama_prompt(text: str, source_url: str) -> str:
    return f"""Perform a full intelligence audit on this content.
Source: {source_url}

CONTENT:
{text}

Analyze for credibility, deception patterns, and capital risk signals."""


# ─────────────────────────────────────────────
# GROQ — LLM
# ─────────────────────────────────────────────
async def query_groq(prompt: str, user_id: Optional[int] = None) -> str:
    import asyncio

    loop = asyncio.get_running_loop()
    history = get_user_memory(user_id) if user_id else []
    messages = (
        [{"role": "system", "content": SENTINEL_SYSTEM_PROMPT}]
        + history
        + [{"role": "user", "content": prompt}]
    )

    def _sync_call():
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.4,
            max_tokens=1200,
        )
        return completion.choices[0].message.content.strip()

    return await loop.run_in_executor(None, _sync_call)


# ─────────────────────────────────────────────
# GROQ — WHISPER
# ─────────────────────────────────────────────
async def transcribe_audio(file_path: str) -> str:
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
                    "Key terms: Liquidity, Contract Address, DexScreener, Market Cap, FDV, "
                    "Token, Solana, Ethereum, Base, ROI, leverage, capital risk, wallet, "
                    "rugpull, holder, volume, price change, whitepaper, AMA."
                ),
            )
        if isinstance(transcription, str):
            return transcription.strip()
        return transcription.text.strip()

    return await loop.run_in_executor(None, _sync_transcribe)


# ─────────────────────────────────────────────
# PRICE ALERT CHECKER (every 60 seconds)
# ─────────────────────────────────────────────
async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE):
    if not price_alerts:
        return

    triggered = []
    for alert in price_alerts:
        try:
            data = await fetch_token_data(alert["ca"])
            current_price = float(data["price_usd"] or 0)
            target = float(alert["target"])
            hit = (
                (alert["direction"] == "above" and current_price >= target)
                or (alert["direction"] == "below" and current_price <= target)
            )
            if hit:
                await context.bot.send_message(
                    chat_id=alert["user_id"],
                    text=(
                        f"SENTINEL ALERT TRIGGERED\n\n"
                        f"Token: {alert['symbol']}\n"
                        f"Condition: Price {alert['direction']} ${target}\n"
                        f"Current Price: ${current_price}\n\n"
                        f"Execute your position. The window may be closing."
                    ),
                )
                triggered.append(alert)
                logger.info(f"Alert fired: {alert['symbol']} @ {current_price}")
        except Exception as e:
            logger.warning(f"Alert check failed for {alert['ca']}: {e}")

    for alert in triggered:
        price_alerts.remove(alert)


# ─────────────────────────────────────────────
# INTENT ROUTER
# ─────────────────────────────────────────────
async def route_intent(
    intent: str,
    text: str,
    user_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    portfolio = user_portfolios.get(user_id, [])

    # ── CRYPTO AUDIT ──
    if intent == "crypto_audit":
        ca = extract_ca(text)
        try:
            token_data = await fetch_token_data(ca)
        except httpx.HTTPStatusError as e:
            await update.message.reply_text(
                f"DexScreener returned HTTP {e.response.status_code}. "
                "Contract may not exist on any tracked chain."
            )
            return
        except httpx.RequestError:
            await update.message.reply_text("DexScreener unreachable. Retry in 60 seconds.")
            return
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
        prompt = build_crypto_prompt(token_data)
        response = await query_groq(prompt)
        await update.message.reply_text(response)

    # ── URL / AMA AUDIT ──
    elif intent == "url_audit":
        url = extract_url(text)
        try:
            content = await fetch_url_content(url)
            if not content:
                await update.message.reply_text("Could not extract readable content from that URL.")
                return
        except httpx.HTTPStatusError as e:
            await update.message.reply_text(f"URL returned HTTP {e.response.status_code}.")
            return
        except httpx.RequestError:
            await update.message.reply_text("Could not reach that URL. It may be down or blocking bots.")
            return
        prompt = build_ama_prompt(content, url)
        response = await query_groq(prompt, user_id=user_id)
        update_user_memory(user_id, text, response)
        await update.message.reply_text(response)

    # ── PORTFOLIO ADD ──
    elif intent == "portfolio_add":
        ca = extract_ca(text)
        if not ca:
            await update.message.reply_text(
                "I can see you want to add a token but I did not find a contract address. "
                "Send the CA alongside your request."
            )
            return
        if ca in portfolio:
            await update.message.reply_text("That token is already in your portfolio.")
            return
        if len(portfolio) >= MAX_PORTFOLIO_SIZE:
            await update.message.reply_text(
                f"Portfolio is at capacity ({MAX_PORTFOLIO_SIZE} tokens). "
                "Tell me which one to remove first."
            )
            return
        portfolio.append(ca)
        user_portfolios[user_id] = portfolio
        try:
            data = await fetch_token_data(ca)
            await update.message.reply_text(
                f"{data['name']} ({data['symbol']}) added to your portfolio.\n"
                f"You are now tracking {len(portfolio)} token(s)."
            )
        except Exception:
            await update.message.reply_text(
                f"Added {ca} to your portfolio. Could not verify token name — "
                "it may not be listed on DexScreener yet."
            )

    # ── PORTFOLIO REMOVE ──
    elif intent == "portfolio_remove":
        ca = extract_ca(text)
        if not ca:
            await update.message.reply_text(
                "Tell me which contract address to remove, or send the CA directly."
            )
            return
        if ca not in portfolio:
            await update.message.reply_text("That token is not in your portfolio.")
            return
        portfolio.remove(ca)
        user_portfolios[user_id] = portfolio
        await update.message.reply_text(f"Removed {ca} from your portfolio.")

    # ── PORTFOLIO SCAN ──
    elif intent == "portfolio_scan":
        if not portfolio:
            await update.message.reply_text(
                "Your portfolio is empty. Send a contract address and tell me to track it."
            )
            return
        await update.message.reply_text(f"Scanning {len(portfolio)} token(s). Stand by.")
        for ca in portfolio:
            try:
                token_data = await fetch_token_data(ca)
                prompt = build_crypto_prompt(token_data)
                response = await query_groq(prompt)
                await update.message.reply_text(response)
            except Exception as e:
                await update.message.reply_text(f"Scan failed for {ca}: {str(e)}")

    # ── PORTFOLIO LIST ──
    elif intent == "portfolio_list":
        if not portfolio:
            await update.message.reply_text(
                "Your portfolio is empty. Send a CA and say you want to track it."
            )
            return
        lines = [f"{i+1}. {ca}" for i, ca in enumerate(portfolio)]
        await update.message.reply_text("YOUR TRACKED TOKENS:\n\n" + "\n".join(lines))

    # ── ALERT SET ──
    elif intent == "alert_set":
        ca = extract_ca(text)
        price = extract_price(text)
        low = text.lower()

        if not ca:
            await update.message.reply_text(
                "I need a contract address to set an alert. "
                "Send the CA and target price in the same message.\n\n"
                "Example: alert me when 0xABC... goes above 0.05"
            )
            return
        if not price:
            await update.message.reply_text(
                "I need a target price.\n"
                "Example: alert me when it hits $0.05"
            )
            return

        if any(w in low for w in ["above", "over", "hits", "reaches", "crosses", "passes", "goes to"]):
            direction = "above"
        elif any(w in low for w in ["below", "under", "drops", "falls", "dips", "goes below"]):
            direction = "below"
        else:
            direction = "above"

        try:
            token_data = await fetch_token_data(ca)
            symbol = token_data["symbol"]
            current_price = token_data["price_usd"]
        except Exception as e:
            await update.message.reply_text(f"Could not verify token on DexScreener: {str(e)}")
            return

        price_alerts.append({
            "user_id": user_id,
            "ca": ca,
            "symbol": symbol,
            "target": price,
            "direction": direction,
        })

        await update.message.reply_text(
            f"Alert set.\n\n"
            f"Token: {symbol}\n"
            f"Condition: Price {direction} ${price}\n"
            f"Current Price: ${current_price}\n\n"
            f"Sentinel is watching. You will be notified when the condition is met."
        )

    # ── ALERT LIST ──
    elif intent == "alert_list":
        user_alerts = [a for a in price_alerts if a["user_id"] == user_id]
        if not user_alerts:
            await update.message.reply_text(
                "No active alerts. Tell me a token CA and target price to set one."
            )
            return
        lines = [
            f"{i+1}. {a['symbol']} — price {a['direction']} ${a['target']}"
            for i, a in enumerate(user_alerts)
        ]
        await update.message.reply_text("ACTIVE PRICE ALERTS:\n\n" + "\n".join(lines))

    # ── ALERT CANCEL ──
    elif intent == "alert_cancel":
        ca = extract_ca(text)
        if not ca:
            user_alerts = [a for a in price_alerts if a["user_id"] == user_id]
            if not user_alerts:
                await update.message.reply_text("You have no active alerts to cancel.")
                return
            await update.message.reply_text(
                "Which token's alert should I cancel? Send the contract address."
            )
            return
        before = len(price_alerts)
        price_alerts[:] = [
            a for a in price_alerts
            if not (a["user_id"] == user_id and a["ca"] == ca)
        ]
        removed = before - len(price_alerts)
        if removed:
            await update.message.reply_text(f"Cancelled {removed} alert(s) for {ca}.")
        else:
            await update.message.reply_text("No alerts found for that token.")

    # ── CLEAR MEMORY ──
    elif intent == "clear_memory":
        clear_user_memory(user_id)
        await update.message.reply_text("Memory cleared. Starting fresh.")

    # ── HELP ──
    elif intent == "help":
        await update.message.reply_text(
            "Sentinel online. Here is what I do:\n\n"
            "CRYPTO AUDIT\n"
            "Send any contract address (CA). I will pull live data from DexScreener and issue a forensic verdict.\n\n"
            "BUSINESS AUDIT\n"
            "Describe any business idea or strategy. I will identify logic leaks and ROI potential.\n\n"
            "WHITEPAPER FORENSICS\n"
            "Send a PDF whitepaper. I will detect fabrication, plagiarism, and red flags.\n\n"
            "AMA AND URL INTELLIGENCE\n"
            "Send any link — AMA, article, announcement. I will audit it for credibility and deception.\n\n"
            "VOICE NOTES\n"
            "Send a voice note. I will transcribe it and route it through the appropriate audit.\n\n"
            "PORTFOLIO TRACKING\n"
            "Say: track this token [CA] — to add it.\n"
            "Say: scan my portfolio — to audit everything at once.\n"
            "Say: show my portfolio — to list tracked tokens.\n"
            "Say: remove this token [CA] — to untrack it.\n\n"
            "PRICE ALERTS\n"
            "Say: alert me when [CA] goes above $0.05 — to set an alert.\n"
            "Say: what are my alerts — to view them.\n"
            "Say: cancel the alert for [CA] — to remove it.\n\n"
            "MEMORY\n"
            "Say: forget everything — to reset the conversation.\n\n"
            "No commands. No syntax. Just talk."
        )

    # ── GENERAL / BUSINESS / GREETING ──
    else:
        response = await query_groq(text, user_id=user_id)
        update_user_memory(user_id, text, response)
        await update.message.reply_text(response)


# ─────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    if not user_input:
        return

    if is_rate_limited(user_id):
        await update.message.reply_text(
            f"Rate limit active. Wait {RATE_LIMIT_SECONDS} seconds between requests."
        )
        return

    logger.info(f"Text [{user_id}]: {user_input[:80]}")
    intent = detect_intent(user_input)
    logger.info(f"Intent: {intent}")

    if intent in ("crypto_audit", "url_audit", "portfolio_scan"):
        await update.message.reply_text("Auditing. Stand by.")

    try:
        await route_intent(intent, user_input, user_id, update, context)
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        await update.message.reply_text(f"Operation failed. Error: {str(e)}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_rate_limited(user_id):
        await update.message.reply_text(f"Rate limit active. Wait {RATE_LIMIT_SECONDS} seconds.")
        return

    logger.info(f"Voice [{user_id}]")
    await update.message.reply_text("Voice note received. Engaging Whisper transcription layer.")

    tmp_path = None
    try:
        voice = update.message.voice
        tg_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await tg_file.download_to_drive(tmp_path)
        logger.info(f"Voice: {tmp_path} | {os.path.getsize(tmp_path)} bytes")

        if os.path.getsize(tmp_path) == 0:
            await update.message.reply_text("Audio arrived empty. Re-record and retry.")
            return

        transcript = await transcribe_audio(tmp_path)
        if not transcript:
            await update.message.reply_text("Whisper returned empty transcription. Audio may be silent or too short.")
            return

        await update.message.reply_text(f"Transcription:\n\n{transcript}\n\nInitiating audit.")

        intent = detect_intent(transcript)
        logger.info(f"Voice intent: {intent}")

        if intent in ("crypto_audit", "url_audit", "portfolio_scan"):
            await update.message.reply_text("Auditing. Stand by.")

        await route_intent(intent, transcript, user_id, update, context)

    except Exception as e:
        logger.error(f"Voice error: {e}", exc_info=True)
        await update.message.reply_text(f"Voice processing failed. Error: {str(e)}")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document

    if is_rate_limited(user_id):
        await update.message.reply_text(f"Rate limit active. Wait {RATE_LIMIT_SECONDS} seconds.")
        return

    if not doc.mime_type or "pdf" not in doc.mime_type.lower():
        await update.message.reply_text(
            "Only PDF documents are supported for whitepaper auditing. "
            "Send the whitepaper as a .pdf file."
        )
        return

    await update.message.reply_text(
        f"PDF received: {doc.file_name}\n"
        "Extracting content. Running whitepaper forensics."
    )

    tmp_path = None
    try:
        tg_file = await context.bot.get_file(doc.file_id)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name

        await tg_file.download_to_drive(tmp_path)
        logger.info(f"PDF: {tmp_path} | {os.path.getsize(tmp_path)} bytes")

        if os.path.getsize(tmp_path) == 0:
            await update.message.reply_text("PDF arrived empty or corrupt. Re-upload and retry.")
            return

        text = extract_pdf_text(tmp_path)
        if not text or len(text) < 100:
            await update.message.reply_text(
                "Could not extract readable text. "
                "This PDF may be image-based (scanned). Sentinel requires text-based PDFs."
            )
            return

        logger.info(f"PDF: {len(text)} chars from {doc.file_name}")
        prompt = build_whitepaper_prompt(text, doc.file_name)
        response = await query_groq(prompt)
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"PDF error: {e}", exc_info=True)
        await update.message.reply_text(f"Whitepaper audit failed. Error: {str(e)}")
    finally:
        if tmp_path:
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
    logger.info(f"Flask heartbeat on port {PORT}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    app.job_queue.run_repeating(check_price_alerts, interval=60, first=15)
    logger.info("Price alert scheduler active.")

    logger.info("Sentinel is live.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
