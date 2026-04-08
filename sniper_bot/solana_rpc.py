from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .http import HttpClient
from .rate_limit import AsyncRateLimiter


UTC = timezone.utc


class SolanaRpcClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.log = logging.getLogger(self.__class__.__name__)
        self.limiter = AsyncRateLimiter(max_calls=settings.rpc_rate_limit_per_sec, period_seconds=1.0)
        self._request_id = 0

    async def rpc(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        data = await self.http.post_json(settings.solana_rpc_http, payload, limiter=self.limiter)
        if data.get("error"):
            raise RuntimeError(f"RPC error for {method}: {data['error']}")
        return data.get("result")

    async def get_signatures_for_address(self, address: str, limit: int = 20, before: str | None = None) -> list[dict[str, Any]]:
        cfg: dict[str, Any] = {"limit": limit, "commitment": "confirmed"}
        if before:
            cfg["before"] = before
        result = await self.rpc("getSignaturesForAddress", [address, cfg])
        return result or []

    async def get_transaction(self, signature: str) -> dict[str, Any] | None:
        return await self.rpc(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": "confirmed",
                },
            ],
        )

    async def get_token_largest_accounts(self, mint: str) -> list[dict[str, Any]]:
        result = await self.rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        return (result or {}).get("value") or []

    def parse_wallet_flows_from_transaction(
        self,
        tx: dict[str, Any],
        token_mint: str,
        *,
        early_cutoff_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        if not tx:
            return []
        block_time = tx.get("blockTime")
        if block_time is None:
            return []
        meta = tx.get("meta") or {}
        pre_balances = meta.get("preTokenBalances") or []
        post_balances = meta.get("postTokenBalances") or []
        if not post_balances:
            return []

        pre_by_index: dict[int, dict[str, Any]] = {
            int(item.get("accountIndex", -1)): item
            for item in pre_balances
            if item.get("mint") == token_mint
        }
        flows: list[dict[str, Any]] = []
        for post in post_balances:
            if post.get("mint") != token_mint:
                continue
            account_index = int(post.get("accountIndex", -1))
            owner = post.get("owner")
            if not owner:
                continue
            post_amount = float(((post.get("uiTokenAmount") or {}).get("uiAmount") or 0.0))
            pre = pre_by_index.get(account_index)
            pre_amount = float((((pre or {}).get("uiTokenAmount") or {}).get("uiAmount") or 0.0))
            delta = post_amount - pre_amount
            if abs(delta) <= 0:
                continue
            side = "buy" if delta > 0 else "sell"
            is_early = bool(early_cutoff_ts is not None and block_time <= early_cutoff_ts and side == "buy")
            flows.append(
                {
                    "wallet_address": owner,
                    "amount_token": abs(delta),
                    "delta_token": delta,
                    "timestamp": datetime.fromtimestamp(block_time, tz=UTC),
                    "is_early": is_early,
                    "side": side,
                }
            )
        flows.sort(key=lambda item: item["amount_token"], reverse=True)
        return flows
