from __future__ import annotations

import logging
from typing import Any

from .config import settings
from .http import HttpClient
from .models import TokenCandidate
from .rate_limit import AsyncRateLimiter


class DexClient:
    BASE = "https://api.dexscreener.com"

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.log = logging.getLogger(self.__class__.__name__)
        self.limiter_60 = AsyncRateLimiter(max_calls=1, period_seconds=1.05)
        self.limiter_300 = AsyncRateLimiter(max_calls=4, period_seconds=1.05)

    async def discover_candidates(self) -> list[TokenCandidate]:
        candidates: dict[str, TokenCandidate] = {}
        if settings.enable_dex_profile_discovery:
            try:
                data = await self.http.get_json(f"{self.BASE}/token-profiles/latest/v1", limiter=self.limiter_60)
                for item in data[: settings.profile_discovery_limit]:
                    if item.get("chainId") != "solana":
                        continue
                    token = TokenCandidate(
                        token_address=item.get("tokenAddress", ""),
                        chain_id="solana",
                        name=item.get("description", "")[:42],
                        description=item.get("description", ""),
                        source="dex_profile",
                        url=item.get("url", ""),
                        links=item.get("links") or [],
                    )
                    if token.token_address:
                        candidates[token.token_address] = token
            except Exception as exc:
                self.log.warning("Dex profile discovery failed: %s", exc)

        if settings.enable_dex_boost_discovery:
            try:
                data = await self.http.get_json(f"{self.BASE}/token-boosts/latest/v1", limiter=self.limiter_60)
                items = data if isinstance(data, list) else [data]
                for item in items[: settings.boost_discovery_limit]:
                    if item.get("chainId") != "solana":
                        continue
                    token_address = item.get("tokenAddress", "")
                    if not token_address:
                        continue
                    token = candidates.get(token_address) or TokenCandidate(
                        token_address=token_address,
                        chain_id="solana",
                        source="dex_boost",
                        url=item.get("url", ""),
                    )
                    token.boosts_active = int(item.get("totalAmount") or item.get("amount") or 0)
                    token.description = token.description or item.get("description", "")
                    token.links = token.links or item.get("links") or []
                    candidates[token_address] = token
            except Exception as exc:
                self.log.warning("Dex boost discovery failed: %s", exc)
        return list(candidates.values())

    async def enrich_token(self, token: TokenCandidate) -> TokenCandidate | None:
        if not token.token_address:
            return None
        url = f"{self.BASE}/token-pairs/v1/solana/{token.token_address}"
        data = await self.http.get_json(url, limiter=self.limiter_300)
        if not isinstance(data, list) or not data:
            return None
        best = max(data, key=self._pair_rank)
        return self._merge_pair_data(token, best)

    def _pair_rank(self, pair: dict[str, Any]) -> tuple[float, float, float]:
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0.0)
        volume = float((pair.get("volume") or {}).get("h1") or 0.0)
        txns = float(((pair.get("txns") or {}).get("m5") or {}).get("buys") or 0) + float(((pair.get("txns") or {}).get("m5") or {}).get("sells") or 0)
        return liquidity, volume, txns

    def _merge_pair_data(self, token: TokenCandidate, pair: dict[str, Any]) -> TokenCandidate:
        base = pair.get("baseToken") or {}
        txns = pair.get("txns") or {}
        m5 = txns.get("m5") or {}
        h1 = txns.get("h1") or {}
        volume = pair.get("volume") or {}
        liquidity = pair.get("liquidity") or {}
        token.symbol = base.get("symbol", token.symbol)
        token.name = base.get("name", token.name)
        token.created_at_ms = pair.get("pairCreatedAt") or token.created_at_ms
        token.pair_address = pair.get("pairAddress", token.pair_address)
        token.url = pair.get("url", token.url)
        token.price_usd = float(pair.get("priceUsd") or 0.0)
        token.liquidity_usd = float(liquidity.get("usd") or 0.0)
        token.volume_m5 = float(volume.get("m5") or 0.0)
        token.volume_h1 = float(volume.get("h1") or 0.0)
        token.txns_m5_buys = int(m5.get("buys") or 0)
        token.txns_m5_sells = int(m5.get("sells") or 0)
        token.txns_h1_buys = int(h1.get("buys") or 0)
        token.txns_h1_sells = int(h1.get("sells") or 0)
        token.info = pair.get("info") or {}
        token.metadata["fdv"] = float(pair.get("fdv") or 0.0)
        token.metadata["market_cap"] = float(pair.get("marketCap") or 0.0)
        token.metadata["dex_id"] = pair.get("dexId", "")
        token.metadata["labels"] = pair.get("labels") or []
        token.metadata["pair_chain"] = pair.get("chainId", "")
        return token
