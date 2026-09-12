"""
sentinel_intel.py
Extension module for Sentinel: reply-context memory, historical price
trend data (GeckoTerminal), and holder concentration data (Blockscout
for EVM chains, Solscan for Solana).

Drop this file next to main.py and import from it. See INTEGRATION NOTES
at the bottom of this file for exact wiring instructions.
"""

import os
import logging
from typing import Optional

import httpx
from telegram import Update

logger = logging.getLogger("Sentinel.Intel")

# ─────────────────────────────────────────────
# ENV — add these to your Render/host environment
# ─────────────────────────────────────────────
BLOCKSCOUT_API_KEY = os.environ.get("BLOCKSCOUT_API_KEY", "")
SOLSCAN_API_KEY = os.environ.get("SOLSCAN_API_KEY", "")

# ─────────────────────────────────────────────
# CHAIN MAPPINGS
# ─────────────────────────────────────────────
# DexScreener chainId string -> GeckoTerminal network slug
GECKOTERMINAL_NETWORK_MAP = {
    "ethereum": "eth",
    "bsc": "bsc",
    "base": "base",
    "solana": "solana",
    "polygon": "polygon_pos",
    "arbitrum": "arbitrum",
    "avalanche": "avax",
}

# DexScreener chainId string -> Blockscout chain_id (numeric, used in api.blockscout.com/{chain_id}/...)
BLOCKSCOUT_CHAIN_ID_MAP = {
    "ethereum": 1,
    "bsc": 56,
    "base": 8453,
}


# ─────────────────────────────────────────────
# 1. REPLY-CONTEXT MEMORY
# ─────────────────────────────────────────────
def get_reply_context(update: Update) -> str:
    """
    If the user replied to a specific earlier message, pull its text so
    Sentinel knows what "this" or "it" refers to.
    """
    replied = update.message.reply_to_message
    if replied and replied.text:
        snippet = replied.text.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + "...[truncated]"
        return f"[Replying to earlier message]: {snippet}\n\n"
    return ""


# ─────────────────────────────────────────────
# 2. HISTORICAL PRICE / LIQUIDITY TREND — GeckoTerminal (no key needed)
# ─────────────────────────────────────────────
async def fetch_token_history(ca: str, chain: str) -> dict:
    """
    Pulls daily OHLCV candles for the token's top pool since pool creation.
    Returns a dict describing the trend, or {"available": False, "reason": ...}
    """
    network = GECKOTERMINAL_NETWORK_MAP.get(chain.lower())
    if not network:
        return {"available": False, "reason": f"No historical data source mapped for chain '{chain}'."}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Find the token's highest-liquidity pool
            pools_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{ca}/pools"
            resp = await client.get(pools_url)
            resp.raise_for_status()
            pools_data = resp.json().get("data", [])

            if not pools_data:
                return {"available": False, "reason": "No pools found on GeckoTerminal for this token."}

            top_pool = max(
                pools_data,
                key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0),
            )
            pool_address = top_pool["attributes"]["address"]

            # Daily candles for the lifetime of the pool
            ohlcv_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/day"
            resp = await client.get(ohlcv_url, params={"aggregate": 1, "limit": 1000})
            resp.raise_for_status()
            candles = (
                resp.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            )

        if not candles:
            return {"available": False, "reason": "No historical candle data returned."}

        # Each candle: [unix_timestamp, open, high, low, close, volume]
        candles.sort(key=lambda c: c[0])
        first_close = candles[0][4]
        last_close = candles[-1][4]
        all_time_high = max(c[2] for c in candles)
        all_time_low = min(c[3] for c in candles)
        days_tracked = len(candles)

        pct_change = None
        if first_close:
            pct_change = round(((last_close - first_close) / first_close) * 100, 2)

        return {
            "available": True,
            "days_of_data": days_tracked,
            "earliest_tracked_price": first_close,
            "latest_tracked_price": last_close,
            "all_time_high": all_time_high,
            "all_time_low": all_time_low,
            "pct_change_since_earliest_data": pct_change,
        }

    except httpx.HTTPStatusError as e:
        logger.warning(f"GeckoTerminal HTTP error for {ca}: {e}")
        return {"available": False, "reason": f"GeckoTerminal returned HTTP {e.response.status_code}."}
    except httpx.RequestError as e:
        logger.warning(f"GeckoTerminal request error for {ca}: {e}")
        return {"available": False, "reason": "GeckoTerminal unreachable."}
    except Exception as e:
        logger.error(f"GeckoTerminal unexpected error for {ca}: {e}", exc_info=True)
        return {"available": False, "reason": "Unexpected error fetching historical data."}


# ─────────────────────────────────────────────
# 3. HOLDER CONCENTRATION — EVM chains via Blockscout Pro API
# ─────────────────────────────────────────────
async def fetch_evm_holder_data(ca: str, chain: str) -> dict:
    """
    Uses Blockscout's unified Pro API (one key, many chains) to pull
    top holder concentration for ERC-20 tokens.
    """
    chain_id = BLOCKSCOUT_CHAIN_ID_MAP.get(chain.lower())
    if not chain_id:
        return {"available": False, "reason": f"No Blockscout mapping for chain '{chain}'."}

    if not BLOCKSCOUT_API_KEY:
        return {"available": False, "reason": "BLOCKSCOUT_API_KEY not configured."}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"https://api.blockscout.com/{chain_id}/api/v2/tokens/{ca}/holders"
            resp = await client.get(url, params={"apikey": BLOCKSCOUT_API_KEY})
            resp.raise_for_status()
            data = resp.json()

        holders = data.get("items", [])
        if not holders:
            return {"available": False, "reason": "No holder data returned."}

        # Compute concentration in top 10 wallets vs total supply if fields present
        total_supply = None
        token_info_url = f"https://api.blockscout.com/{chain_id}/api/v2/tokens/{ca}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp2 = await client.get(token_info_url, params={"apikey": BLOCKSCOUT_API_KEY})
            if resp2.status_code == 200:
                total_supply = resp2.json().get("total_supply")

        top_10_balance = sum(
            float(h.get("value", 0) or 0) for h in holders[:10]
        )

        concentration_pct = None
        if total_supply:
            try:
                concentration_pct = round((top_10_balance / float(total_supply)) * 100, 2)
            except (ValueError, ZeroDivisionError):
                concentration_pct = None

        return {
            "available": True,
            "holder_count_sampled": len(holders),
            "top_10_holder_concentration_pct": concentration_pct,
        }

    except httpx.HTTPStatusError as e:
        logger.warning(f"Blockscout HTTP error for {ca}: {e}")
        return {"available": False, "reason": f"Blockscout returned HTTP {e.response.status_code}."}
    except httpx.RequestError as e:
        logger.warning(f"Blockscout request error for {ca}: {e}")
        return {"available": False, "reason": "Blockscout unreachable."}
    except Exception as e:
        logger.error(f"Blockscout unexpected error for {ca}: {e}", exc_info=True)
        return {"available": False, "reason": "Unexpected error fetching holder data."}


# ─────────────────────────────────────────────
# 4. HOLDER CONCENTRATION — Solana via Solscan
# ─────────────────────────────────────────────
async def fetch_solana_holder_data(ca: str) -> dict:
    """
    Uses Solscan's token/holders endpoint. Handles the case where the
    key is on a plan that doesn't cover this endpoint (403/402).
    """
    if not SOLSCAN_API_KEY:
        return {"available": False, "reason": "SOLSCAN_API_KEY not configured."}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = "https://pro-api.solscan.io/v2.0/token/holders"
            headers = {"token": SOLSCAN_API_KEY}
            params = {"address": ca, "page": 1, "page_size": 20}
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code in (401, 402, 403):
            return {
                "available": False,
                "reason": f"Solscan denied the request (HTTP {resp.status_code}). "
                          "Your key may not cover this endpoint/tier.",
            }
        resp.raise_for_status()
        payload = resp.json()

        holders = payload.get("data", [])
        if not holders:
            return {"available": False, "reason": "No holder data returned by Solscan."}

        top_10_amount = sum(float(h.get("amount", 0) or 0) for h in holders[:10])

        return {
            "available": True,
            "holder_count_sampled": len(holders),
            "top_10_holder_raw_total": top_10_amount,
        }

    except httpx.RequestError as e:
        logger.warning(f"Solscan request error for {ca}: {e}")
        return {"available": False, "reason": "Solscan unreachable."}
    except Exception as e:
        logger.error(f"Solscan unexpected error for {ca}: {e}", exc_info=True)
        return {"available": False, "reason": "Unexpected error fetching Solscan holder data."}


# ─────────────────────────────────────────────
# 5. AGGREGATOR — call this one function from main.py
# ─────────────────────────────────────────────
async def gather_deep_intel(ca: str, chain: str) -> dict:
    """
    Runs history + holder lookups for a token and returns a combined dict
    ready to be formatted into the audit prompt. Never raises — every
    sub-fetch fails gracefully with an "available": False reason.
    """
    history = await fetch_token_history(ca, chain)

    if chain.lower() == "solana":
        holders = await fetch_solana_holder_data(ca)
    else:
        holders = await fetch_evm_holder_data(ca, chain)

    return {"history": history, "holders": holders}


def format_deep_intel_for_prompt(intel: dict) -> str:
    """
    Turns the gather_deep_intel() output into a plain-text block to append
    to the crypto audit prompt sent to Groq.
    """
    lines = ["\nDEEP INTELLIGENCE DATA:"]

    history = intel.get("history", {})
    if history.get("available"):
        lines.append(
            f"- Historical data: {history['days_of_data']} days tracked. "
            f"Earliest tracked price ${history['earliest_tracked_price']}, "
            f"current tracked price ${history['latest_tracked_price']}. "
            f"All-time high ${history['all_time_high']}, all-time low ${history['all_time_low']}. "
            f"Change since earliest tracked data: {history['pct_change_since_earliest_data']}%."
        )
    else:
        lines.append(f"- Historical data: unavailable ({history.get('reason', 'unknown')}).")

    holders = intel.get("holders", {})
    if holders.get("available"):
        if "top_10_holder_concentration_pct" in holders and holders["top_10_holder_concentration_pct"] is not None:
            lines.append(
                f"- Holder concentration: top 10 wallets hold "
                f"{holders['top_10_holder_concentration_pct']}% of total supply "
                f"(sampled {holders['holder_count_sampled']} holders)."
            )
        else:
            lines.append(
                f"- Holder data: sampled {holders['holder_count_sampled']} holders, "
                "supply-percentage unavailable."
            )
    else:
        lines.append(f"- Holder concentration: unavailable ({holders.get('reason', 'unknown')}).")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# INTEGRATION NOTES (read before wiring in)
# ─────────────────────────────────────────────
#
# 1. Add env vars on your host (Render dashboard -> Environment):
#      BLOCKSCOUT_API_KEY = <your free key from blockscout.com Pro API signup>
#      SOLSCAN_API_KEY    = <the key you already have>
#
# 2. In main.py, add:
#      from sentinel_intel import get_reply_context, gather_deep_intel, format_deep_intel_for_prompt
#
# 3. In handle_text(), right after extracting user_input, add:
#      reply_context = get_reply_context(update)
#      user_input_for_llm = reply_context + user_input if reply_context else user_input
#    Then pass user_input_for_llm (not raw user_input) into route_intent() for the
#    text that goes to the LLM. Keep using raw user_input for detect_intent()/extract_ca()
#    since regex patterns shouldn't be confused by the injected reply text.
#
# 4. In route_intent(), inside the "crypto_audit" branch, after fetch_token_data()
#    succeeds and before build_crypto_prompt(), add:
#      intel = await gather_deep_intel(ca, token_data["chain"])
#      intel_block = format_deep_intel_for_prompt(intel)
#      prompt = build_crypto_prompt(token_data) + intel_block
#
# 5. Update SENTINEL_SYSTEM_PROMPT's crypto audit section to tell the model to use
#    the new data. Add this line under "INPUT TYPE 1":
#
#      "If DEEP INTELLIGENCE DATA is present, factor holder concentration into
#      Exit Liquidity Concentration and factor historical price trend into
#      Structural Alpha Decay. If a data point is unavailable, state that
#      plainly rather than guessing — never fabricate a number."
#
#    That last sentence matters: without it, the model may invent holder
#    percentages when the real API call failed. Explicit unavailability
#    instructions stop that.
