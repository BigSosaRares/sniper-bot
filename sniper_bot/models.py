from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class TokenCandidate:
    token_address: str
    chain_id: str = "solana"
    symbol: str = ""
    name: str = ""
    description: str = ""
    source: str = "unknown"
    created_at_ms: int | None = None
    pair_address: str = ""
    url: str = ""
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_m5: float = 0.0
    volume_h1: float = 0.0
    txns_m5_buys: int = 0
    txns_m5_sells: int = 0
    txns_h1_buys: int = 0
    txns_h1_sells: int = 0
    boosts_active: int = 0
    links: list[dict[str, Any]] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    first_seen_at: datetime = field(default_factory=utc_now)

    @property
    def age_seconds(self) -> float:
        if self.created_at_ms:
            return max(0.0, (utc_now().timestamp() * 1000 - self.created_at_ms) / 1000)
        return max(0.0, (utc_now() - self.first_seen_at).total_seconds())

    @property
    def display_name(self) -> str:
        return self.symbol or self.name or self.token_address[:8]


@dataclass(slots=True)
class WalletSignal:
    wallet_address: str
    token_address: str
    signature: str
    amount_token: float
    amount_sol: float | None
    timestamp: datetime
    is_early: bool
    side: str = "buy"
    token_share_estimate: float = 0.0
    is_whale: bool = False


@dataclass(slots=True)
class WalletStats:
    wallet_address: str
    early_entries: int = 0
    total_entries: int = 0
    wins: int = 0
    losses: int = 0
    median_peak_return: float = 1.0
    wallet_score: float = 0.0
    last_seen_at: datetime | None = None


@dataclass(slots=True)
class ScoreBreakdown:
    total_score: float
    watch_threshold: float
    hot_threshold: float
    label: str
    reasons: list[str]
    raw: dict[str, float]


@dataclass(slots=True)
class TokenRiskReport:
    risk_score: float
    rating: str
    flags: list[str]
    human_summary: str
    dev_sell_share: float = 0.0
    whale_sell_events: int = 0
    top_holder_pct: float = 0.0
    top10_holder_pct: float = 0.0


@dataclass(slots=True)
class ExitSignal:
    should_exit: bool
    urgency: str
    score: float
    reasons: list[str]
    human_summary: str


@dataclass(slots=True)
class HumanVerdict:
    label: str
    confidence: str
    summary: str
    action: str
