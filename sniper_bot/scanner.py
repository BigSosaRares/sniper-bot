from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .config import settings
from .db import Database
from .dex_client import DexClient
from .dynamic_score import DynamicScorer
from .http import HttpClient
from .logging_utils import configure_logging
from .models import TokenCandidate
from .solana_rpc import SolanaRpcClient
from .telegram import TelegramNotifier
from .wallet_score import WalletScorer
from .wallet_tracker import WalletTracker


UTC = timezone.utc


class SniperScanner:
    def __init__(self) -> None:
        configure_logging()
        self.log = logging.getLogger(self.__class__.__name__)
        self.db = Database()
        self._tracked: dict[str, TokenCandidate] = {}
        self._last_entry_alert_at: dict[str, datetime] = {}
        self._last_exit_alert_at: dict[str, datetime] = {}

    async def run(self) -> None:
        async with HttpClient() as http:
            dex = DexClient(http)
            rpc = SolanaRpcClient(http)
            wallet_scorer = WalletScorer(self.db)
            wallet_tracker = WalletTracker(rpc, self.db, wallet_scorer)
            dynamic_scorer = DynamicScorer(self.db)
            notifier = TelegramNotifier(http)

            self.log.info("scanner started")
            while True:
                try:
                    await self._cycle(dex, wallet_tracker, dynamic_scorer, notifier)
                except Exception as exc:
                    self.log.exception("scanner cycle failed: %s", exc)
                await asyncio.sleep(settings.scan_interval_seconds)

    async def _cycle(
        self,
        dex: DexClient,
        wallet_tracker: WalletTracker,
        dynamic_scorer: DynamicScorer,
        notifier: TelegramNotifier,
    ) -> None:
        candidates = await dex.discover_candidates()
        self.log.info("discovered %s candidates", len(candidates))
        enriched = await self._enrich_candidates(dex, candidates)
        self._cleanup_tracked()

        for token in enriched:
            if not self._passes_basic_filters(token):
                continue
            tracked = self._tracked.get(token.token_address)
            if tracked:
                token.first_seen_at = tracked.first_seen_at
            self._tracked[token.token_address] = token

            wallet_metrics = await wallet_tracker.scan_token(token)
            score = dynamic_scorer.score(token, wallet_metrics)
            risk = dynamic_scorer.assess_risk(token, wallet_metrics)
            exit_signal = dynamic_scorer.assess_exit(token, wallet_metrics, score)
            verdict = dynamic_scorer.humanize(token, score, risk, exit_signal, wallet_metrics)
            self.db.save_token_snapshot(token, score)

            self.log.info(
                "%s %s score=%.2f liq=%.0f vol5m=%.0f smart=%.2f early=%s whales=%s risk=%s exit=%s",
                score.label,
                token.display_name,
                score.total_score,
                token.liquidity_usd,
                token.volume_m5,
                wallet_metrics.get("smart_money_count", 0.0),
                int(wallet_metrics.get("early_buyer_count", 0.0)),
                int(wallet_metrics.get("whale_buys", 0.0)),
                risk.rating,
                exit_signal.urgency,
            )

            if score.label in {"WATCH", "HOT", "PREPUMP"} and self._should_entry_alert(token.token_address):
                await notifier.send_entry_alert(token, score, wallet_metrics, risk, verdict)
                self._last_entry_alert_at[token.token_address] = datetime.now(tz=UTC)
                self.db.mark_entry_alert(token.token_address)

            if exit_signal.should_exit and self._should_exit_alert(token.token_address):
                await notifier.send_exit_alert(token, score, wallet_metrics, exit_signal, verdict)
                self._last_exit_alert_at[token.token_address] = datetime.now(tz=UTC)
                self.db.mark_exit_alert(token.token_address, exit_signal.score)

        self.db.cleanup_old_rows()

    async def _enrich_candidates(self, dex: DexClient, candidates: list[TokenCandidate]) -> list[TokenCandidate]:
        results: list[TokenCandidate] = []
        batch = candidates[: settings.max_enrich_batch]
        enriched = await asyncio.gather(*(dex.enrich_token(token) for token in batch), return_exceptions=True)
        for item in enriched:
            if isinstance(item, TokenCandidate):
                results.append(item)
        return results

    def _passes_basic_filters(self, token: TokenCandidate) -> bool:
        if not token.token_address or token.chain_id != "solana":
            return False
        if token.age_seconds > settings.max_token_age_minutes * 60:
            return False
        text = f"{token.symbol} {token.name} {token.description}".lower()
        if any(word in text for word in settings.blacklist_words):
            return False
        if token.symbol.upper() in settings.blacklist_symbols:
            return False
        if token.liquidity_usd < min(settings.fresh_min_liquidity_watch, settings.revival_min_liquidity_watch):
            return False
        return True

    def _should_entry_alert(self, token_address: str) -> bool:
        last = self._last_entry_alert_at.get(token_address)
        now = datetime.now(tz=UTC)
        if last and now - last < timedelta(seconds=settings.alert_cooldown_seconds):
            return False
        return True

    def _should_exit_alert(self, token_address: str) -> bool:
        last = self._last_exit_alert_at.get(token_address)
        now = datetime.now(tz=UTC)
        if last and now - last < timedelta(seconds=settings.exit_alert_cooldown_seconds):
            return False
        return True

    def _cleanup_tracked(self) -> None:
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.track_token_seconds)
        stale = [k for k, token in self._tracked.items() if token.first_seen_at < cutoff]
        for key in stale:
            self._tracked.pop(key, None)
        if len(self._tracked) > settings.max_tracked_tokens:
            ordered = sorted(self._tracked.items(), key=lambda item: item[1].first_seen_at)
            for key, _ in ordered[: len(self._tracked) - settings.max_tracked_tokens]:
                self._tracked.pop(key, None)
