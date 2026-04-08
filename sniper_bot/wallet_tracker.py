from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from statistics import mean
from typing import Any

from .config import settings
from .db import Database
from .models import TokenCandidate, WalletSignal
from .solana_rpc import SolanaRpcClient
from .wallet_score import WalletScorer


class WalletTracker:
    def __init__(self, rpc: SolanaRpcClient, db: Database, scorer: WalletScorer) -> None:
        self.rpc = rpc
        self.db = db
        self.scorer = scorer
        self.log = logging.getLogger(self.__class__.__name__)
        self._seen_signatures_by_token: dict[str, set[str]] = {}
        self._seen_wallets_by_token: dict[str, set[str]] = {}
        self._semaphore = asyncio.Semaphore(settings.wallet_scan_max_concurrent)

    async def scan_token(self, token: TokenCandidate) -> dict[str, float]:
        if not settings.enable_wallet_tracker or not token.token_address:
            return self._empty_metrics()
        async with self._semaphore:
            try:
                return await self._scan_token_inner(token)
            except Exception as exc:
                self.log.exception("wallet scan failed for token=%s symbol=%s: %s", token.token_address, token.symbol, exc)
                return self._empty_metrics()

    async def _scan_token_inner(self, token: TokenCandidate) -> dict[str, float]:
        signatures = await self.rpc.get_signatures_for_address(token.token_address, limit=settings.wallet_tracker_tx_limit)
        largest_accounts = await self.rpc.get_token_largest_accounts(token.token_address)
        holder_dist = self._summarize_holder_distribution(largest_accounts)

        seen_signatures = self._seen_signatures_by_token.setdefault(token.token_address, set())
        seen_wallets = self._seen_wallets_by_token.setdefault(token.token_address, set())

        early_cutoff_ts: int | None = None
        if token.created_at_ms:
            created_ts = int(token.created_at_ms / 1000)
            if created_ts > 0:
                early_cutoff_ts = created_ts + settings.wallet_early_window_seconds

        smart_money_count = 0
        early_buyer_count = 0
        total_wallet_buys = 0
        smart_money_score_sum = 0.0
        qualified_wallets = 0
        parsed_buys_total = 0
        whale_buys = 0
        whale_sells = 0
        smart_money_outflows = 0
        dev_sell_amount = 0.0
        dev_buy_amount = 0.0
        total_sell_amount = 0.0
        total_buy_amount = 0.0
        whale_wallet_scores: list[float] = []
        wallet_amounts: list[float] = []
        early_wallet_candidates: set[str] = set()
        flow_counts_by_wallet: dict[str, dict[str, int]] = defaultdict(lambda: {"buy": 0, "sell": 0})

        for item in reversed(signatures):
            sig = item.get("signature")
            if not sig or sig in seen_signatures:
                continue
            tx = await self.rpc.get_transaction(sig)
            if not tx:
                seen_signatures.add(sig)
                continue

            flows = self.rpc.parse_wallet_flows_from_transaction(tx, token.token_address, early_cutoff_ts=early_cutoff_ts)
            if settings.wallet_min_token_amount > 0:
                flows = [flow for flow in flows if float(flow["amount_token"]) >= settings.wallet_min_token_amount]

            for flow in flows:
                wallet = str(flow["wallet_address"])
                side = str(flow["side"])
                amount_token = float(flow["amount_token"])
                token_share_est = amount_token / max(token.liquidity_usd / max(token.price_usd, 1e-9), 1.0) if token.price_usd > 0 and token.liquidity_usd > 0 else 0.0
                is_whale = token_share_est >= settings.whale_min_token_share

                if side == "buy":
                    parsed_buys_total += 1
                    total_buy_amount += amount_token
                else:
                    total_sell_amount += amount_token

                is_early = bool(flow["is_early"])
                if side == "buy" and not is_early and early_buyer_count < 3:
                    is_early = True
                if side == "buy" and is_early:
                    early_wallet_candidates.add(wallet)

                signal = WalletSignal(
                    wallet_address=wallet,
                    token_address=token.token_address,
                    signature=f"{sig}:{wallet}:{side}",
                    amount_token=amount_token,
                    amount_sol=None,
                    timestamp=flow["timestamp"],
                    is_early=is_early,
                    side=side,
                    token_share_estimate=round(token_share_est, 6),
                    is_whale=is_whale,
                )
                inserted = self.db.save_wallet_signal(signal)
                if not inserted:
                    continue

                flow_counts_by_wallet[wallet][side] += 1
                stats = self.scorer.update_wallet_from_signal(signal)
                if signal.is_whale:
                    whale_wallet_scores.append(stats.wallet_score)
                is_smart = self.scorer.is_smart_money(wallet)

                if side == "buy":
                    wallet_amounts.append(amount_token)
                    if wallet not in seen_wallets:
                        seen_wallets.add(wallet)
                        qualified_wallets += 1
                        total_wallet_buys += 1
                        if signal.is_early:
                            early_buyer_count += 1
                        if is_smart:
                            smart_money_count += 1
                            smart_money_score_sum += stats.wallet_score
                    if signal.is_whale:
                        whale_buys += 1
                    if wallet in early_wallet_candidates:
                        dev_buy_amount += amount_token
                else:
                    if signal.is_whale:
                        whale_sells += 1
                    if is_smart:
                        smart_money_outflows += 1
                    if wallet in early_wallet_candidates:
                        dev_sell_amount += amount_token

                self.log.info(
                    "wallet_flow token=%s wallet=%s side=%s early=%s whale=%s score=%.4f smart=%s",
                    token.display_name,
                    wallet[:8],
                    side,
                    signal.is_early,
                    signal.is_whale,
                    stats.wallet_score,
                    is_smart,
                )

            seen_signatures.add(sig)
            if qualified_wallets >= settings.wallet_track_max_wallets_per_token:
                break

        avg_smart_wallet_score = (smart_money_score_sum / smart_money_count) if smart_money_count else 0.0
        avg_whale_score = mean(whale_wallet_scores) if whale_wallet_scores else 0.0
        dev_sell_share = dev_sell_amount / max(dev_buy_amount, 1e-9) if dev_buy_amount > 0 else 0.0
        buy_sell_ratio = total_buy_amount / max(total_sell_amount, 1.0)

        self.log.info(
            "wallet_scan_done token=%s parsed_buys=%s qualified=%s early_buyers=%s smart_count=%s whale_buys=%s whale_sells=%s dev_sell_share=%.2f avg_smart=%.4f",
            token.display_name,
            parsed_buys_total,
            qualified_wallets,
            early_buyer_count,
            smart_money_count,
            whale_buys,
            whale_sells,
            dev_sell_share,
            avg_smart_wallet_score,
        )
        return {
            "smart_money_count": float(smart_money_count),
            "early_buyer_count": float(early_buyer_count),
            "total_wallet_buys": float(total_wallet_buys),
            "avg_smart_wallet_score": round(avg_smart_wallet_score, 4),
            "qualified_wallets": float(qualified_wallets),
            "whale_buys": float(whale_buys),
            "whale_sells": float(whale_sells),
            "avg_whale_wallet_score": round(avg_whale_score, 4),
            "dev_sell_share": round(dev_sell_share, 4),
            "buy_sell_ratio_wallet": round(buy_sell_ratio, 4),
            "smart_money_outflows": float(smart_money_outflows),
            "top_holder_pct": round(holder_dist["top1_pct"], 2),
            "top10_holder_pct": round(holder_dist["top10_pct"], 2),
        }

    def _summarize_holder_distribution(self, largest_accounts: list[dict[str, Any]]) -> dict[str, float]:
        amounts: list[float] = []
        for item in largest_accounts:
            ui_amount = ((item.get("uiAmount") if isinstance(item, dict) else None) or 0.0)
            try:
                amounts.append(float(ui_amount))
            except (TypeError, ValueError):
                continue
        if not amounts:
            return {"top1_pct": 0.0, "top10_pct": 0.0}
        total = sum(amounts) or 1.0
        top1 = amounts[0] / total * 100.0
        top10 = sum(amounts[:10]) / total * 100.0
        return {"top1_pct": top1, "top10_pct": top10}

    def _empty_metrics(self) -> dict[str, float]:
        return {
            "smart_money_count": 0.0,
            "early_buyer_count": 0.0,
            "total_wallet_buys": 0.0,
            "avg_smart_wallet_score": 0.0,
            "qualified_wallets": 0.0,
            "whale_buys": 0.0,
            "whale_sells": 0.0,
            "avg_whale_wallet_score": 0.0,
            "dev_sell_share": 0.0,
            "buy_sell_ratio_wallet": 0.0,
            "smart_money_outflows": 0.0,
            "top_holder_pct": 0.0,
            "top10_holder_pct": 0.0,
        }
