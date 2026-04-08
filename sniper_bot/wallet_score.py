from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

from .config import settings
from .db import Database
from .models import WalletSignal, WalletStats


@dataclass(slots=True)
class _CachedWalletStats:
    stats: WalletStats
    expires_at: float


class WalletScorer:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[str, _CachedWalletStats] = {}

    def get_wallet_stats(self, wallet_address: str) -> WalletStats:
        cached = self._cache.get(wallet_address)
        now = time.time()
        if cached and cached.expires_at > now:
            return cached.stats
        stats = self.db.get_wallet_stats(wallet_address)
        self._cache[wallet_address] = _CachedWalletStats(stats=stats, expires_at=now + settings.wallet_score_cache_ttl)
        return stats

    def update_wallet_from_signal(self, signal: WalletSignal) -> WalletStats:
        stats = self.get_wallet_stats(signal.wallet_address)
        if signal.side == "buy":
            stats.total_entries += 1
            if signal.is_early:
                stats.early_entries += 1
        stats.last_seen_at = signal.timestamp
        self._refresh_outcomes(stats)
        live_score = self._compute_live_score(stats)
        historical_score = self._compute_historical_score(stats)
        stats.wallet_score = self._compute_final_score(stats, live_score, historical_score)
        self.db.upsert_wallet_stats(stats)
        self._cache[signal.wallet_address] = _CachedWalletStats(
            stats=stats,
            expires_at=time.time() + settings.wallet_score_cache_ttl,
        )
        return stats

    def _refresh_outcomes(self, stats: WalletStats) -> None:
        rows = self.db.get_wallet_signals(stats.wallet_address, limit=100)
        peak_returns: list[float] = []
        wins = 0
        losses = 0
        for row in rows:
            if row["side"] != "buy":
                continue
            outcome = self.db.get_token_outcome(row["token_address"])
            if not outcome:
                continue
            first_price, peak_price = outcome
            if first_price <= 0:
                continue
            peak_return = peak_price / first_price
            peak_returns.append(peak_return)
            if peak_return >= 2.0:
                wins += 1
            elif peak_return <= 0.85:
                losses += 1
        stats.wins = wins
        stats.losses = losses
        stats.median_peak_return = statistics.median(peak_returns) if peak_returns else 1.0

    def _compute_live_score(self, stats: WalletStats) -> float:
        early_ratio = stats.early_entries / max(1, stats.total_entries)
        activity_confidence = min(1.0, stats.total_entries / 5.0)
        live_score = (0.65 * early_ratio) + (0.35 * activity_confidence)
        if stats.early_entries >= 1:
            live_score = max(live_score, settings.wallet_bootstrap_min_score)
        if early_ratio >= 0.6:
            live_score += 0.06
        return max(0.0, min(1.0, round(live_score, 4)))

    def _compute_historical_score(self, stats: WalletStats) -> float:
        history = max(1, stats.wins + stats.losses)
        win_rate = stats.wins / history if history else 0.0
        normalized_return = max(0.0, min(1.0, (stats.median_peak_return - 1.0) / 2.5))
        history_confidence = min(1.0, history / 8.0)
        historical_score = (0.50 * win_rate) + (0.30 * normalized_return) + (0.20 * history_confidence)
        return max(0.0, min(1.0, round(historical_score, 4)))

    def _compute_final_score(self, stats: WalletStats, live_score: float, historical_score: float) -> float:
        if stats.total_entries <= 3:
            final_score = max(live_score, settings.wallet_bootstrap_min_score)
            if stats.early_entries >= 1:
                final_score = max(final_score, settings.wallet_early_boost_score)
            return round(min(1.0, final_score), 4)
        history_count = stats.wins + stats.losses
        history_weight = min(0.7, history_count / 10.0)
        live_weight = 1.0 - history_weight
        final_score = (live_score * live_weight) + (historical_score * history_weight)
        if stats.early_entries >= 2 and stats.total_entries >= 2:
            final_score += 0.04
        return round(max(0.0, min(1.0, final_score)), 4)

    def is_smart_money(self, wallet_address: str) -> bool:
        stats = self.get_wallet_stats(wallet_address)
        if stats.total_entries >= settings.smart_money_min_history and stats.wallet_score >= settings.smart_money_min_score:
            return True
        early_ratio = stats.early_entries / max(1, stats.total_entries)
        return bool(
            stats.total_entries >= 1
            and stats.early_entries >= 1
            and stats.wallet_score >= settings.wallet_min_live_score_for_smart
            and early_ratio >= 0.5
        )
