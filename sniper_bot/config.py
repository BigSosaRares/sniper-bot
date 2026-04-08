from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


@dataclass(slots=True)
class Settings:
    enable_telegram: bool = _get_bool("ENABLE_TELEGRAM", False)
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    debug: bool = _get_bool("DEBUG", False)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    enable_db: bool = _get_bool("ENABLE_DB", True)
    db_file: Path = Path(os.getenv("DB_FILE", "sniper_scanner.db"))

    solana_rpc_http: str = os.getenv("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
    http_timeout: int = _get_int("HTTP_TIMEOUT", 20)
    http_max_retries: int = _get_int("HTTP_MAX_RETRIES", 5)
    http_backoff_base: float = _get_float("HTTP_BACKOFF_BASE", 1.0)
    rpc_rate_limit_per_sec: int = _get_int("RPC_RATE_LIMIT_PER_SEC", 4)

    scan_interval_seconds: int = _get_int("SCAN_INTERVAL_SECONDS", 20)
    discovery_interval_seconds: int = _get_int("DISCOVERY_INTERVAL_SECONDS", 45)
    cleanup_interval: int = _get_int("CLEANUP_INTERVAL", 60)

    max_tracked_tokens: int = _get_int("MAX_TRACKED_TOKENS", 250)
    max_token_age_minutes: int = _get_int("MAX_TOKEN_AGE_MINUTES", 240)
    ultra_early_max_age_seconds: int = _get_int("ULTRA_EARLY_MAX_AGE_SECONDS", 180)
    track_token_seconds: int = _get_int("TRACK_TOKEN_SECONDS", 21600)
    alert_cooldown_seconds: int = _get_int("ALERT_COOLDOWN_SECONDS", 180)
    exit_alert_cooldown_seconds: int = _get_int("EXIT_ALERT_COOLDOWN_SECONDS", 240)

    enable_dex_profile_discovery: bool = _get_bool("ENABLE_DEX_PROFILE_DISCOVERY", True)
    enable_dex_boost_discovery: bool = _get_bool("ENABLE_DEX_BOOST_DISCOVERY", True)
    profile_discovery_limit: int = _get_int("PROFILE_DISCOVERY_LIMIT", 30)
    boost_discovery_limit: int = _get_int("BOOST_DISCOVERY_LIMIT", 30)
    max_enrich_batch: int = _get_int("MAX_ENRICH_BATCH", 20)

    dynamic_score_lookback: int = _get_int("DYNAMIC_SCORE_LOOKBACK", 250)
    dynamic_score_min_samples: int = _get_int("DYNAMIC_SCORE_MIN_SAMPLES", 20)

    watch_score: int = _get_int("WATCH_SCORE", 18)
    hot_score: int = _get_int("HOT_SCORE", 34)
    prepump_score: int = _get_int("PREPUMP_SCORE", 28)
    exit_score: int = _get_int("EXIT_SCORE", 62)

    fresh_max_age_minutes: int = _get_int("FRESH_MAX_AGE_MINUTES", 35)
    fresh_min_liquidity_watch: float = _get_float("FRESH_MIN_LIQUIDITY_WATCH", 1000)
    fresh_min_liquidity_hot: float = _get_float("FRESH_MIN_LIQUIDITY_HOT", 7000)
    fresh_min_local_buy_watch: float = _get_float("FRESH_MIN_LOCAL_BUY_WATCH", 50)
    fresh_min_local_buy_hot: float = _get_float("FRESH_MIN_LOCAL_BUY_HOT", 280)
    fresh_min_local_trades_watch: int = _get_int("FRESH_MIN_LOCAL_TRADES_WATCH", 2)
    fresh_min_local_trades_hot: int = _get_int("FRESH_MIN_LOCAL_TRADES_HOT", 5)

    revival_min_age_minutes: int = _get_int("REVIVAL_MIN_AGE_MINUTES", 20)
    revival_min_liquidity_watch: float = _get_float("REVIVAL_MIN_LIQUIDITY_WATCH", 3000)
    revival_min_liquidity_hot: float = _get_float("REVIVAL_MIN_LIQUIDITY_HOT", 12000)
    revival_min_local_buy_watch: float = _get_float("REVIVAL_MIN_LOCAL_BUY_WATCH", 100)
    revival_min_local_buy_hot: float = _get_float("REVIVAL_MIN_LOCAL_BUY_HOT", 450)
    revival_min_volume_5m_watch: float = _get_float("REVIVAL_MIN_VOLUME_5M_WATCH", 1000)
    revival_min_volume_5m_hot: float = _get_float("REVIVAL_MIN_VOLUME_5M_HOT", 7000)
    revival_min_dex_txns_5m_watch: int = _get_int("REVIVAL_MIN_DEX_TXNS_5M_WATCH", 3)
    revival_min_dex_txns_5m_hot: int = _get_int("REVIVAL_MIN_DEX_TXNS_5M_HOT", 8)
    revival_min_ratio_watch: float = _get_float("REVIVAL_MIN_RATIO_WATCH", 1.10)
    revival_min_ratio_hot: float = _get_float("REVIVAL_MIN_RATIO_HOT", 1.40)

    enable_wallet_tracker: bool = _get_bool("ENABLE_WALLET_TRACKER", True)
    wallet_tracker_tx_limit: int = _get_int("WALLET_TRACKER_TX_LIMIT", 20)
    wallet_scan_max_concurrent: int = _get_int("WALLET_SCAN_MAX_CONCURRENT", 2)
    wallet_early_window_seconds: int = _get_int("WALLET_EARLY_WINDOW_SECONDS", 240)
    wallet_track_max_wallets_per_token: int = _get_int("WALLET_TRACK_MAX_WALLETS_PER_TOKEN", 10)
    wallet_score_cache_ttl: int = _get_int("WALLET_SCORE_CACHE_TTL", 900)
    wallet_min_token_amount: float = _get_float("WALLET_MIN_TOKEN_AMOUNT", 0.0)
    whale_min_token_share: float = _get_float("WHALE_MIN_TOKEN_SHARE", 0.01)
    whale_min_sol_equivalent: float = _get_float("WHALE_MIN_SOL_EQUIVALENT", 1.0)

    smart_money_min_score: float = _get_float("SMART_MONEY_MIN_SCORE", 0.30)
    smart_money_min_history: int = _get_int("SMART_MONEY_MIN_HISTORY", 1)
    smart_money_bonus_max: int = _get_int("SMART_MONEY_BONUS_MAX", 18)
    wallet_bootstrap_min_score: float = _get_float("WALLET_BOOTSTRAP_MIN_SCORE", 0.22)
    wallet_early_boost_score: float = _get_float("WALLET_EARLY_BOOST_SCORE", 0.30)
    wallet_min_live_score_for_smart: float = _get_float("WALLET_MIN_LIVE_SCORE_FOR_SMART", 0.26)

    dev_dump_sell_share_alert: float = _get_float("DEV_DUMP_SELL_SHARE_ALERT", 0.20)
    top_holder_warn_pct: float = _get_float("TOP_HOLDER_WARN_PCT", 18.0)
    top10_holder_warn_pct: float = _get_float("TOP10_HOLDER_WARN_PCT", 65.0)
    max_whale_sell_events_for_ok: int = _get_int("MAX_WHALE_SELL_EVENTS_FOR_OK", 0)
    exit_whale_dump_pct: float = _get_float("EXIT_WHALE_DUMP_PCT", 0.08)
    exit_sell_pressure_ratio: float = _get_float("EXIT_SELL_PRESSURE_RATIO", 1.35)
    exit_smart_money_outflow: int = _get_int("EXIT_SMART_MONEY_OUTFLOW", 1)
    exit_volume_fade_ratio: float = _get_float("EXIT_VOLUME_FADE_RATIO", 0.35)

    blacklist_words: list[str] = field(default_factory=list)
    blacklist_symbols: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.blacklist_words = _get_csv("BLACKLIST_WORDS")
        self.blacklist_symbols = [s.upper() for s in _get_csv("BLACKLIST_SYMBOLS")]


settings = Settings()
