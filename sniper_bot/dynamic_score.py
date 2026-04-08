from __future__ import annotations

import math
from dataclasses import dataclass

from .config import settings
from .db import Database
from .models import ExitSignal, HumanVerdict, ScoreBreakdown, TokenCandidate, TokenRiskReport


@dataclass(slots=True)
class DynamicThresholds:
    watch: float
    hot: float


class DynamicScorer:
    def __init__(self, db: Database) -> None:
        self.db = db

    def score(self, token: TokenCandidate, wallet_metrics: dict[str, float]) -> ScoreBreakdown:
        thresholds = self.compute_thresholds()
        recent = self.db.get_recent_token_metrics(settings.dynamic_score_lookback)

        liquidity_score = self._percentile(token.liquidity_usd, [float(x.get("liquidity_usd") or 0.0) for x in recent], self._bounded(token.liquidity_usd / 12000.0))
        volume_score = self._percentile(token.volume_m5, [float(x.get("volume_m5") or 0.0) for x in recent], self._bounded(token.volume_m5 / 12000.0))
        buy_score = self._percentile(token.txns_m5_buys, [float(x.get("txns_m5_buys") or 0.0) for x in recent], self._bounded(token.txns_m5_buys / 18.0))
        imbalance = token.txns_m5_buys / max(1, token.txns_m5_sells)
        imbalance_score = self._bounded((imbalance - 0.8) / 2.2)
        ultra_early_score = 1.0 if token.age_seconds <= settings.ultra_early_max_age_seconds else self._bounded(1 - (token.age_seconds / 1800.0))
        boost_score = self._percentile(token.boosts_active, [float(x.get("boosts_active") or 0.0) for x in recent], self._bounded(token.boosts_active / 50.0))
        smart_money_component = self._bounded((wallet_metrics.get("smart_money_count", 0.0) * 0.18) + (wallet_metrics.get("avg_smart_wallet_score", 0.0) * 0.82))
        early_buyer_component = self._bounded(wallet_metrics.get("early_buyer_count", 0.0) / 8.0)
        whale_component = self._bounded((wallet_metrics.get("whale_buys", 0.0) * 0.15) + (wallet_metrics.get("avg_whale_wallet_score", 0.0) * 0.85))
        prepump_component = self._bounded((imbalance_score * 0.35) + (ultra_early_score * 0.35) + (whale_component * 0.30))

        raw = {
            "liquidity": liquidity_score * 18,
            "volume_m5": volume_score * 16,
            "buy_pressure": buy_score * 14,
            "imbalance": imbalance_score * 10,
            "ultra_early": ultra_early_score * 14,
            "boosts": boost_score * 6,
            "smart_money": smart_money_component * settings.smart_money_bonus_max,
            "early_wallets": early_buyer_component * 8,
            "whales": whale_component * 8,
            "prepump": prepump_component * 8,
        }
        total = round(sum(raw.values()), 2)
        reasons = self._reasons(token, wallet_metrics, raw)
        label = "IGNORE"
        if total >= thresholds.hot:
            label = "HOT"
        elif total >= max(float(settings.prepump_score), thresholds.watch - 1):
            if ultra_early_score > 0.55 and imbalance_score > 0.45:
                label = "PREPUMP"
            else:
                label = "WATCH"
        elif total >= thresholds.watch:
            label = "WATCH"
        return ScoreBreakdown(total_score=total, watch_threshold=thresholds.watch, hot_threshold=thresholds.hot, label=label, reasons=reasons, raw=raw)

    def compute_thresholds(self) -> DynamicThresholds:
        recent = self.db.get_recent_token_metrics(settings.dynamic_score_lookback)
        score_values = [float(x.get("score_total") or 0.0) for x in recent if x.get("score_total") is not None]
        if len(score_values) < settings.dynamic_score_min_samples:
            return DynamicThresholds(watch=float(settings.watch_score), hot=float(settings.hot_score))
        score_values.sort()
        watch = max(float(settings.watch_score), self._quantile(score_values, 0.72))
        hot = max(float(settings.hot_score), self._quantile(score_values, 0.90))
        if hot <= watch:
            hot = watch + 6.0
        return DynamicThresholds(watch=round(watch, 2), hot=round(hot, 2))

    def assess_risk(self, token: TokenCandidate, wallet_metrics: dict[str, float]) -> TokenRiskReport:
        score = 0.0
        flags: list[str] = []
        dev_sell_share = float(wallet_metrics.get("dev_sell_share", 0.0))
        top_holder_pct = float(wallet_metrics.get("top_holder_pct", 0.0))
        top10_holder_pct = float(wallet_metrics.get("top10_holder_pct", 0.0))
        whale_sell_events = int(wallet_metrics.get("whale_sells", 0.0))

        if dev_sell_share >= settings.dev_dump_sell_share_alert:
            score += 35
            flags.append("early/dev wallets are selling hard")
        if whale_sell_events > settings.max_whale_sell_events_for_ok:
            score += min(20, whale_sell_events * 8)
            flags.append("whales already started dumping")
        if top_holder_pct >= settings.top_holder_warn_pct:
            score += 18
            flags.append("top holder concentration is high")
        if top10_holder_pct >= settings.top10_holder_warn_pct:
            score += 15
            flags.append("top 10 holders control too much")
        if token.txns_m5_sells > token.txns_m5_buys:
            score += 10
            flags.append("sell pressure is stronger than buy pressure")
        if token.liquidity_usd < settings.fresh_min_liquidity_watch:
            score += 12
            flags.append("liquidity is still thin")
        if not flags:
            flags.append("no major red flags detected yet")

        if score >= 55:
            rating = "HIGH_RISK"
            human = "Arată periculos. Eu nu l-aș numi ok acum."
        elif score >= 28:
            rating = "MIXED"
            human = "Are ceva semnale bune, dar și riscuri clare. Intrare mică sau deloc."
        else:
            rating = "RELATIV_OK"
            human = "Relativ ok pentru un memecoin, dar tot rămâne foarte riscant."

        return TokenRiskReport(
            risk_score=round(score, 2),
            rating=rating,
            flags=flags,
            human_summary=human,
            dev_sell_share=dev_sell_share,
            whale_sell_events=whale_sell_events,
            top_holder_pct=top_holder_pct,
            top10_holder_pct=top10_holder_pct,
        )

    def assess_exit(self, token: TokenCandidate, wallet_metrics: dict[str, float], score: ScoreBreakdown) -> ExitSignal:
        reasons: list[str] = []
        exit_score = 0.0
        buy_sell_ratio_wallet = float(wallet_metrics.get("buy_sell_ratio_wallet", 0.0))
        smart_outflows = int(wallet_metrics.get("smart_money_outflows", 0.0))
        dev_sell_share = float(wallet_metrics.get("dev_sell_share", 0.0))
        whale_sells = float(wallet_metrics.get("whale_sells", 0.0))

        snapshots = self.db.get_recent_snapshots(token.token_address, limit=6)
        current_vol = token.volume_m5
        prev_vol = float(snapshots[1]["volume_m5"]) if len(snapshots) > 1 else current_vol
        vol_ratio = current_vol / max(prev_vol, 1.0)

        if token.txns_m5_sells / max(1, token.txns_m5_buys) >= settings.exit_sell_pressure_ratio:
            exit_score += 24
            reasons.append("sell pressure overtook buys")
        if whale_sells > 0 and dev_sell_share >= settings.exit_whale_dump_pct:
            exit_score += 24
            reasons.append("large early wallets are unloading")
        if smart_outflows >= settings.exit_smart_money_outflow:
            exit_score += 15
            reasons.append("smart money wallets started exiting")
        if vol_ratio <= settings.exit_volume_fade_ratio and score.total_score < settings.exit_score:
            exit_score += 14
            reasons.append("momentum faded fast")
        if score.total_score < score.watch_threshold and len(snapshots) >= 2:
            exit_score += 10
            reasons.append("score lost the watch zone")

        if exit_score >= 55:
            urgency = "EXIT_NOW"
            human = "Aș ieși repede sau aș marca profit acum."
        elif exit_score >= 30:
            urgency = "TRIM"
            human = "Aș reduce expunerea și aș muta stop-ul mai sus."
        else:
            urgency = "HOLD"
            human = "Încă nu pare exit clar."

        return ExitSignal(should_exit=urgency != "HOLD", urgency=urgency, score=round(exit_score, 2), reasons=reasons or ["momentum still intact"], human_summary=human)

    def humanize(self, token: TokenCandidate, score: ScoreBreakdown, risk: TokenRiskReport, exit_signal: ExitSignal, wallet_metrics: dict[str, float]) -> HumanVerdict:
        if risk.rating == "HIGH_RISK":
            return HumanVerdict(
                label="NU E OK",
                confidence="ridicată",
                summary=f"{token.display_name} are prea multe red flags: {', '.join(risk.flags[:3])}.",
                action="stai departe sau doar scalp foarte mic",
            )
        if exit_signal.should_exit and exit_signal.urgency == "EXIT_NOW":
            return HumanVerdict(
                label="IA PROFIT / IEȘI",
                confidence="medie",
                summary=f"{token.display_name} a slăbit: {', '.join(exit_signal.reasons[:3])}.",
                action="marchează profit sau ieși complet",
            )
        if score.label in {"HOT", "PREPUMP"} and risk.rating == "RELATIV_OK":
            return HumanVerdict(
                label="RELATIV OK",
                confidence="medie",
                summary=f"{token.display_name} are flux bun, {int(wallet_metrics.get('early_buyer_count', 0))} early buyers și risc controlabil momentan.",
                action="intrare mică, confirmare pe volum și urmărește exit-ul",
            )
        return HumanVerdict(
            label="MIXT",
            confidence="scăzută",
            summary=f"{token.display_name} are semnale bune, dar nu destule pentru încredere mare.",
            action="așteaptă confirmare",
        )

    def _percentile(self, value: float, series: list[float], fallback: float) -> float:
        usable = [x for x in series if x is not None]
        if len(usable) < settings.dynamic_score_min_samples:
            return self._bounded(fallback)
        usable.sort()
        below = sum(1 for x in usable if x <= value)
        return self._bounded(below / len(usable))

    def _quantile(self, series: list[float], q: float) -> float:
        if not series:
            return 0.0
        idx = (len(series) - 1) * q
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo == hi:
            return series[lo]
        return series[lo] + (series[hi] - series[lo]) * (idx - lo)

    def _bounded(self, value: float) -> float:
        return max(0.0, min(1.0, value))

    def _reasons(self, token: TokenCandidate, wallet_metrics: dict[str, float], raw: dict[str, float]) -> list[str]:
        reasons: list[str] = []
        if raw["ultra_early"] >= 10:
            reasons.append("ultra early")
        if raw["smart_money"] >= 8:
            reasons.append("smart money wallets detected")
        if raw["whales"] >= 4:
            reasons.append("whales are accumulating")
        if token.liquidity_usd >= settings.fresh_min_liquidity_watch:
            reasons.append(f"liq ${token.liquidity_usd:,.0f}")
        if token.volume_m5 > 0:
            reasons.append(f"vol5m ${token.volume_m5:,.0f}")
        if token.txns_m5_buys > token.txns_m5_sells:
            reasons.append(f"buys {token.txns_m5_buys}/{token.txns_m5_sells}")
        if wallet_metrics.get("early_buyer_count", 0.0) >= 2:
            reasons.append(f"early buyers {int(wallet_metrics['early_buyer_count'])}")
        if token.boosts_active > 0:
            reasons.append(f"boosts {token.boosts_active}")
        return reasons[:6]
