from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings
from .models import ScoreBreakdown, TokenCandidate, WalletSignal, WalletStats


UTC = timezone.utc


class Database:
    def __init__(self, db_file: Path | None = None) -> None:
        self.db_file = db_file or settings.db_file
        self.enabled = settings.enable_db
        if self.enabled:
            self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS token_snapshots (
                    token_address TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    symbol TEXT,
                    name TEXT,
                    source TEXT,
                    created_at_ms INTEGER,
                    pair_address TEXT,
                    price_usd REAL,
                    liquidity_usd REAL,
                    volume_m5 REAL,
                    volume_h1 REAL,
                    txns_m5_buys INTEGER,
                    txns_m5_sells INTEGER,
                    txns_h1_buys INTEGER,
                    txns_h1_sells INTEGER,
                    boosts_active INTEGER,
                    score_total REAL,
                    score_label TEXT,
                    reasons TEXT,
                    PRIMARY KEY (token_address, ts)
                );

                CREATE TABLE IF NOT EXISTS wallet_signals (
                    signature TEXT PRIMARY KEY,
                    wallet_address TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    amount_token REAL,
                    amount_sol REAL,
                    timestamp TEXT NOT NULL,
                    is_early INTEGER NOT NULL,
                    side TEXT NOT NULL DEFAULT 'buy',
                    token_share_estimate REAL NOT NULL DEFAULT 0,
                    is_whale INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS wallet_stats (
                    wallet_address TEXT PRIMARY KEY,
                    early_entries INTEGER NOT NULL,
                    total_entries INTEGER NOT NULL,
                    wins INTEGER NOT NULL,
                    losses INTEGER NOT NULL,
                    median_peak_return REAL NOT NULL,
                    wallet_score REAL NOT NULL,
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS token_outcomes (
                    token_address TEXT PRIMARY KEY,
                    first_price_usd REAL,
                    peak_price_usd REAL,
                    first_seen_at TEXT,
                    last_updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS token_state (
                    token_address TEXT PRIMARY KEY,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    last_entry_alert_at TEXT,
                    last_exit_alert_at TEXT,
                    last_label TEXT,
                    last_exit_score REAL DEFAULT 0,
                    peak_score REAL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_wallet_signals_token_ts ON wallet_signals(token_address, timestamp);
                CREATE INDEX IF NOT EXISTS idx_wallet_signals_wallet_ts ON wallet_signals(wallet_address, timestamp);
                CREATE INDEX IF NOT EXISTS idx_token_snapshots_token_ts ON token_snapshots(token_address, ts);
                """
            )

    def save_token_snapshot(self, token: TokenCandidate, score: ScoreBreakdown) -> None:
        if not self.enabled:
            return
        now = datetime.now(tz=UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO token_snapshots (
                    token_address, ts, symbol, name, source, created_at_ms, pair_address,
                    price_usd, liquidity_usd, volume_m5, volume_h1,
                    txns_m5_buys, txns_m5_sells, txns_h1_buys, txns_h1_sells,
                    boosts_active, score_total, score_label, reasons
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token.token_address,
                    now,
                    token.symbol,
                    token.name,
                    token.source,
                    token.created_at_ms,
                    token.pair_address,
                    token.price_usd,
                    token.liquidity_usd,
                    token.volume_m5,
                    token.volume_h1,
                    token.txns_m5_buys,
                    token.txns_m5_sells,
                    token.txns_h1_buys,
                    token.txns_h1_sells,
                    token.boosts_active,
                    score.total_score,
                    score.label,
                    " | ".join(score.reasons),
                ),
            )
            row = conn.execute(
                "SELECT first_price_usd, peak_price_usd FROM token_outcomes WHERE token_address = ?",
                (token.token_address,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO token_outcomes(token_address, first_price_usd, peak_price_usd, first_seen_at, last_updated_at) VALUES (?, ?, ?, ?, ?)",
                    (token.token_address, token.price_usd, token.price_usd, now, now),
                )
            else:
                peak_price = max(float(row["peak_price_usd"] or 0.0), token.price_usd)
                conn.execute(
                    "UPDATE token_outcomes SET peak_price_usd = ?, last_updated_at = ? WHERE token_address = ?",
                    (peak_price, now, token.token_address),
                )

            state = conn.execute(
                "SELECT peak_score, first_seen_at FROM token_state WHERE token_address = ?",
                (token.token_address,),
            ).fetchone()
            if state is None:
                conn.execute(
                    "INSERT INTO token_state(token_address, first_seen_at, last_seen_at, last_label, peak_score) VALUES (?, ?, ?, ?, ?)",
                    (token.token_address, now, now, score.label, score.total_score),
                )
            else:
                peak_score = max(float(state["peak_score"] or 0.0), score.total_score)
                conn.execute(
                    "UPDATE token_state SET last_seen_at = ?, last_label = ?, peak_score = ? WHERE token_address = ?",
                    (now, score.label, peak_score, token.token_address),
                )

    def save_wallet_signal(self, signal: WalletSignal) -> bool:
        if not self.enabled:
            return True
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO wallet_signals(
                    signature, wallet_address, token_address, amount_token, amount_sol,
                    timestamp, is_early, side, token_share_estimate, is_whale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signature,
                    signal.wallet_address,
                    signal.token_address,
                    signal.amount_token,
                    signal.amount_sol,
                    signal.timestamp.isoformat(),
                    1 if signal.is_early else 0,
                    signal.side,
                    signal.token_share_estimate,
                    1 if signal.is_whale else 0,
                ),
            )
            return cur.rowcount > 0

    def get_wallet_stats(self, wallet_address: str) -> WalletStats:
        if not self.enabled:
            return WalletStats(wallet_address=wallet_address)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM wallet_stats WHERE wallet_address = ?",
                (wallet_address,),
            ).fetchone()
            if row is None:
                return WalletStats(wallet_address=wallet_address)
            return WalletStats(
                wallet_address=wallet_address,
                early_entries=int(row["early_entries"]),
                total_entries=int(row["total_entries"]),
                wins=int(row["wins"]),
                losses=int(row["losses"]),
                median_peak_return=float(row["median_peak_return"]),
                wallet_score=float(row["wallet_score"]),
                last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None,
            )

    def upsert_wallet_stats(self, stats: WalletStats) -> None:
        if not self.enabled:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO wallet_stats(wallet_address, early_entries, total_entries, wins, losses, median_peak_return, wallet_score, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    early_entries = excluded.early_entries,
                    total_entries = excluded.total_entries,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    median_peak_return = excluded.median_peak_return,
                    wallet_score = excluded.wallet_score,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    stats.wallet_address,
                    stats.early_entries,
                    stats.total_entries,
                    stats.wins,
                    stats.losses,
                    stats.median_peak_return,
                    stats.wallet_score,
                    stats.last_seen_at.isoformat() if stats.last_seen_at else None,
                ),
            )

    def get_recent_token_metrics(self, limit: int) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT token_address, ts, liquidity_usd, volume_m5, txns_m5_buys, txns_m5_sells,
                       boosts_active, score_total
                FROM token_snapshots
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_snapshots(self, token_address: str, limit: int = 12) -> list[sqlite3.Row]:
        if not self.enabled:
            return []
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM token_snapshots WHERE token_address = ? ORDER BY ts DESC LIMIT ?",
                (token_address, limit),
            ).fetchall()

    def get_token_outcome(self, token_address: str) -> tuple[float, float] | None:
        if not self.enabled:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT first_price_usd, peak_price_usd FROM token_outcomes WHERE token_address = ?",
                (token_address,),
            ).fetchone()
            if row is None:
                return None
            return float(row["first_price_usd"] or 0.0), float(row["peak_price_usd"] or 0.0)

    def get_wallet_signals(self, wallet_address: str, limit: int = 50) -> list[sqlite3.Row]:
        if not self.enabled:
            return []
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM wallet_signals WHERE wallet_address = ? ORDER BY timestamp DESC LIMIT ?",
                (wallet_address, limit),
            ).fetchall()

    def get_token_wallet_flows(self, token_address: str, limit: int = 200) -> list[sqlite3.Row]:
        if not self.enabled:
            return []
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM wallet_signals WHERE token_address = ? ORDER BY timestamp DESC LIMIT ?",
                (token_address, limit),
            ).fetchall()

    def mark_entry_alert(self, token_address: str) -> None:
        if not self.enabled:
            return
        now = datetime.now(tz=UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO token_state(token_address, first_seen_at, last_seen_at, last_entry_alert_at) VALUES (?, ?, ?, ?) ON CONFLICT(token_address) DO UPDATE SET last_seen_at = excluded.last_seen_at, last_entry_alert_at = excluded.last_entry_alert_at",
                (token_address, now, now, now),
            )

    def mark_exit_alert(self, token_address: str, exit_score: float) -> None:
        if not self.enabled:
            return
        now = datetime.now(tz=UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO token_state(token_address, first_seen_at, last_seen_at, last_exit_alert_at, last_exit_score) VALUES (?, ?, ?, ?, ?) ON CONFLICT(token_address) DO UPDATE SET last_seen_at = excluded.last_seen_at, last_exit_alert_at = excluded.last_exit_alert_at, last_exit_score = excluded.last_exit_score",
                (token_address, now, now, now, exit_score),
            )

    def get_token_state(self, token_address: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM token_state WHERE token_address = ?",
                (token_address,),
            ).fetchone()
            return dict(row) if row else None

    def cleanup_old_rows(self) -> None:
        if not self.enabled:
            return
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.track_token_seconds)
        with self.connect() as conn:
            conn.execute("DELETE FROM token_snapshots WHERE ts < ?", (cutoff.isoformat(),))
            conn.execute("DELETE FROM wallet_signals WHERE timestamp < ?", (cutoff.isoformat(),))
