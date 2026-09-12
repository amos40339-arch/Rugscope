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
    "robinhood": "robinhood",
}

# DexScreener chainId string -> Blockscout chain_id (numeric, used in the
# unified api.blockscout.com/{chain_id}/... Pro API).
BLOCKSCOUT_CHAIN_ID_MAP = {
    "ethereum": 1,
    "bsc": 56,
    "base": 8453,
    "robinhood": 4663,
}

# Some chains (especially very new ones) may not be onboarded to the unified
# Pro API yet but do run their own free public Blockscout instance. List
# those as a fallback base URL, tried if the unified endpoint 404s.
BLOCKSCOUT_INSTANCE_FALLBACK = {
    "robinhood": "https://robinhoodchain.blockscout.com",
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
    Pulls pool data for the token and, where available, daily OHLCV candles
    for its highest-liquidity pool. Falls back to short-term price-change
    data already present on the pool object if OHLCV candles aren't
    populated yet (common on brand-new chains). Also flags pool
    proliferation — many near-empty decoy pools around one real pool is a
    manipulation/noise signal worth surfacing on its own.
    """
    network = GECKOTERMINAL_NETWORK_MAP.get(chain.lower())
    if not network:
        return {"available": False, "reason": f"No historical data source mapped for chain '{chain}'."}

    def reserve_usd(pool: dict) -> float:
        try:
            return float((pool.get("attributes") or {}).get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            pools_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{ca}/pools"
            resp = await client.get(pools_url)
            resp.raise_for_status()
            pools_data = resp.json().get("data", [])

            if not pools_data:
                return {"available": False, "reason": "No pools found on GeckoTerminal for this token."}

            pools_sorted = sorted(pools_data, key=reserve_usd, reverse=True)
            top_pool = pools_sorted[0]
            top_attrs = top_pool.get("attributes", {})
            pool_address = top_attrs.get("address")

            # Pool proliferation / decoy-pool signal.
            total_pools = len(pools_data)
            decoy_pools = sum(1 for p in pools_data if reserve_usd(p) < 100)
            earliest_pool_ts = None
            for p in pools_data:
                ts = (p.get("attributes") or {}).get("pool_created_at")
                if ts and (earliest_pool_ts is None or ts < earliest_pool_ts):
                    earliest_pool_ts = ts

            result = {
                "available": True,
                "top_pool_liquidity_usd": reserve_usd(top_pool),
                "top_pool_created_at": top_attrs.get("pool_created_at"),
                "earliest_known_pool_created_at": earliest_pool_ts,
                "total_pools_found": total_pools,
                "low_liquidity_decoy_pools": decoy_pools,
                "short_term_price_change_pct": top_attrs.get("price_change_percentage", {}),
                "candle_history": None,  # filled in below if available
            }

            if pool_address:
                ohlcv_url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}/ohlcv/day"
                ohlcv_resp = await client.get(ohlcv_url, params={"aggregate": 1, "limit": 1000})
                if ohlcv_resp.status_code == 200:
                    candles = (
                        ohlcv_resp.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                    )
                    if candles:
                        candles.sort(key=lambda c: c[0])
                        first_close = candles[0][4]
                        last_close = candles[-1][4]
                        pct_change = None
                        if first_close:
                            pct_change = round(((last_close - first_close) / first_close) * 100, 2)
                        result["candle_history"] = {
                            "days_of_candle_data": len(candles),
                            "earliest_tracked_price": first_close,
                            "latest_tracked_price": last_close,
                            "all_time_high": max(c[2] for c in candles),
                            "all_time_low": min(c[3] for c in candles),
                            "pct_change_since_earliest_data": pct_change,
                        }

            return result

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
async def _blockscout_holders_from_base(base_url: str, ca: str, use_key: bool) -> Optional[dict]:
    """
    Tries to pull holders + total supply from a given Blockscout base URL
    (either the unified api.blockscout.com/{chain_id} or a dedicated
    per-chain instance like robinhoodchain.blockscout.com).
    Returns None on failure so the caller can try a fallback; raises nothing.
    """
    params = {"apikey": BLOCKSCOUT_API_KEY} if (use_key and BLOCKSCOUT_API_KEY) else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        holders_resp = await client.get(f"{base_url}/api/v2/tokens/{ca}/holders", params=params)
        if holders_resp.status_code != 200:
            return None
        holders = holders_resp.json().get("items", [])
        if not holders:
            return None

        total_supply = None
        info_resp = await client.get(f"{base_url}/api/v2/tokens/{ca}", params=params)
        if info_resp.status_code == 200:
            total_supply = info_resp.json().get("total_supply")

    top_10_balance = sum(float(h.get("value", 0) or 0) for h in holders[:10])
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


async def fetch_evm_holder_data(ca: str, chain: str) -> dict:
    """
    Uses Blockscout to pull top holder concentration for ERC-20 tokens.
    Tries the unified Pro API (one key, many chains) first; if that chain
    isn't onboarded there yet, falls back to a dedicated per-chain
    Blockscout instance if one is known (see BLOCKSCOUT_INSTANCE_FALLBACK).
    """
    chain_key = chain.lower()
    chain_id = BLOCKSCOUT_CHAIN_ID_MAP.get(chain_key)

    try:
        if chain_id:
            result = await _blockscout_holders_from_base(
                f"https://api.blockscout.com/{chain_id}", ca, use_key=True
            )
            if result:
                return result

        fallback_base = BLOCKSCOUT_INSTANCE_FALLBACK.get(chain_key)
        if fallback_base:
            result = await _blockscout_holders_from_base(fallback_base, ca, use_key=False)
            if result:
                return result

        if not chain_id and not fallback_base:
            return {"available": False, "reason": f"No Blockscout mapping for chain '{chain}'."}

        return {"available": False, "reason": "Blockscout returned no holder data from any known endpoint."}

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
            f"- Top pool liquidity: ${history['top_pool_liquidity_usd']:,.2f}, "
            f"created {history.get('top_pool_created_at', 'unknown date')}."
        )
        if history.get("earliest_known_pool_created_at"):
            lines.append(
                f"- Earliest known pool for this token created "
                f"{history['earliest_known_pool_created_at']} (approximate on-chain age)."
            )

        total_pools = history.get("total_pools_found", 0)
        decoy_pools = history.get("low_liquidity_decoy_pools", 0)
        if total_pools > 1:
            if decoy_pools >= total_pools - 1 and decoy_pools >= 3:
                lines.append(
                    f"- Pool proliferation flag: {total_pools} pools found, {decoy_pools} of them "
                    f"near-empty (<$100 liquidity) with no real trading activity. Only one pool "
                    f"carries real volume. Pattern consistent with decoy/spam pool creation — "
                    f"treat as a manipulation or noise signal, not necessarily a scam indicator "
                    f"on its own."
                )
            else:
                lines.append(f"- {total_pools} pools found for this token, {decoy_pools} of them low-liquidity.")

        candle = history.get("candle_history")
        if candle:
            lines.append(
                f"- Historical daily candles: {candle['days_of_candle_data']} days tracked. "
                f"Earliest tracked price ${candle['earliest_tracked_price']}, "
                f"current tracked price ${candle['latest_tracked_price']}. "
                f"All-time high ${candle['all_time_high']}, all-time low ${candle['all_time_low']}. "
                f"Change since earliest tracked data: {candle['pct_change_since_earliest_data']}%."
            )
        else:
            short_term = history.get("short_term_price_change_pct") or {}
            if short_term:
                lines.append(
                    f"- Daily candle history not yet populated for this chain/pool. "
                    f"Short-term price change available instead: "
                    f"1h {short_term.get('h1', 'N/A')}%, 6h {short_term.get('h6', 'N/A')}%, "
                    f"24h {short_term.get('h24', 'N/A')}%."
                )
            else:
                lines.append("- No historical or short-term trend data available for this pool.")
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
