from __future__ import annotations

import html
import logging
from urllib.parse import quote_plus

from .config import settings
from .http import HttpClient
from .models import ExitSignal, HumanVerdict, ScoreBreakdown, TokenCandidate, TokenRiskReport
from .rate_limit import AsyncRateLimiter


class TelegramNotifier:
    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.log = logging.getLogger(self.__class__.__name__)
        self.enabled = bool(settings.enable_telegram and settings.telegram_bot_token and settings.telegram_chat_id)
        self.limiter = AsyncRateLimiter(max_calls=1, period_seconds=1.0)

    async def send_entry_alert(
        self,
        token: TokenCandidate,
        score: ScoreBreakdown,
        wallet_metrics: dict[str, float],
        risk: TokenRiskReport,
        verdict: HumanVerdict,
    ) -> None:
        if not self.enabled:
            return
        text = self._format_entry_message(token, score, wallet_metrics, risk, verdict)
        await self._send(text)

    async def send_exit_alert(
        self,
        token: TokenCandidate,
        score: ScoreBreakdown,
        wallet_metrics: dict[str, float],
        exit_signal: ExitSignal,
        verdict: HumanVerdict,
    ) -> None:
        if not self.enabled:
            return
        text = self._format_exit_message(token, score, wallet_metrics, exit_signal, verdict)
        await self._send(text)

    async def _send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            await self.http.post_json(url, payload, limiter=self.limiter)
        except Exception as exc:
            self.log.warning("Telegram send failed: %s", exc)

    def _format_entry_message(self, token: TokenCandidate, score: ScoreBreakdown, wallet_metrics: dict[str, float], risk: TokenRiskReport, verdict: HumanVerdict) -> str:
        name = html.escape(token.display_name)
        dexscreener = token.url or f"https://dexscreener.com/solana/{quote_plus(token.pair_address or token.token_address)}"
        reasons = html.escape(", ".join(score.reasons) or "dynamic score")
        flags = html.escape(", ".join(risk.flags[:3]))
        return (
            f"<b>{html.escape(score.label)}</b> | <b>{name}</b>\n"
            f"Verdict: <b>{html.escape(verdict.label)}</b> ({html.escape(verdict.confidence)})\n"
            f"Pe românește: {html.escape(verdict.summary)}\n"
            f"Ce aș face: <b>{html.escape(verdict.action)}</b>\n"
            f"Score: <b>{score.total_score:.2f}</b> | Risk: <b>{risk.rating}</b> ({risk.risk_score:.0f})\n"
            f"Age: {int(token.age_seconds)}s | Liq: ${token.liquidity_usd:,.0f} | Vol5m: ${token.volume_m5:,.0f}\n"
            f"Buys/Sells 5m: {token.txns_m5_buys}/{token.txns_m5_sells} | Boosts: {token.boosts_active}\n"
            f"Early buyers: {int(wallet_metrics.get('early_buyer_count', 0.0))} | Smart money: {int(wallet_metrics.get('smart_money_count', 0.0))}\n"
            f"Whale buys/sells: {int(wallet_metrics.get('whale_buys', 0.0))}/{int(wallet_metrics.get('whale_sells', 0.0))}\n"
            f"Top holder: {wallet_metrics.get('top_holder_pct', 0.0):.1f}% | Top10: {wallet_metrics.get('top10_holder_pct', 0.0):.1f}%\n"
            f"Dev sell share: {wallet_metrics.get('dev_sell_share', 0.0):.2f}\n"
            f"Why: {reasons}\n"
            f"Flags: {flags}\n"
            f"Mint: <code>{html.escape(token.token_address)}</code>\n"
            f"<a href=\"{html.escape(dexscreener)}\">DexScreener</a>"
        )

    def _format_exit_message(self, token: TokenCandidate, score: ScoreBreakdown, wallet_metrics: dict[str, float], exit_signal: ExitSignal, verdict: HumanVerdict) -> str:
        name = html.escape(token.display_name)
        reasons = html.escape(", ".join(exit_signal.reasons[:4]))
        dexscreener = token.url or f"https://dexscreener.com/solana/{quote_plus(token.pair_address or token.token_address)}"
        return (
            f"<b>EXIT {html.escape(exit_signal.urgency)}</b> | <b>{name}</b>\n"
            f"Pe românește: {html.escape(exit_signal.human_summary)}\n"
            f"Verdict: <b>{html.escape(verdict.label)}</b>\n"
            f"Exit score: <b>{exit_signal.score:.2f}</b> | Current score: {score.total_score:.2f}\n"
            f"Reasons: {reasons}\n"
            f"Whale sells: {int(wallet_metrics.get('whale_sells', 0.0))} | Smart outflows: {int(wallet_metrics.get('smart_money_outflows', 0.0))}\n"
            f"Dev sell share: {wallet_metrics.get('dev_sell_share', 0.0):.2f}\n"
            f"<a href=\"{html.escape(dexscreener)}\">DexScreener</a>"
        )
