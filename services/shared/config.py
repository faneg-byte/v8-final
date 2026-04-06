"""
Central configuration — all tunable parameters for the V8 Engine.
Environment variables override defaults for deployment flexibility.

PROPRIETARY: Parameter values are calibrated intellectual property.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SOODEConfig:
    """SOODE cascading mesh parameters."""
    micro_window: int = 5
    meso_window: int = 15
    macro_window: int = 30
    meso_alpha: float = 0.7    # EMA smoothing for meso grip
    macro_alpha: float = 0.6   # EMA smoothing for macro grip
    dna_alpha: float = 0.3     # EMA smoothing for dna grip (most inertia)
    shock_ratio: float = 1.6   # micro/meso ratio for Micro-Shock
    shock_floor: float = 0.25  # micro minimum for Micro-Shock
    surge_ratio: float = 0.4   # micro/dna ratio for Surging
    surge_ceiling: float = 0.10  # micro maximum for Surging
    decline_threshold: float = 0.25  # dna threshold for Decline


@dataclass(frozen=True)
class WaveCollapseConfig:
    """Wave collapse and channel parameters."""
    spe_threshold: float = 76.0  # Minimum SPE to emit Live Alpha signal
    channels: tuple = ("garch", "lstm", "bayesian", "cnn")
    default_weights: dict = field(default_factory=lambda: {
        "garch": 0.25, "lstm": 0.25, "bayesian": 0.30, "cnn": 0.20
    })


@dataclass(frozen=True)
class WFOConfig:
    """Walk-forward optimization parameters."""
    start_after_years: int = 5  # WFO begins after 5 years of data
    train_window_years: int = 4
    test_window_months: int = 6
    step_months: int = 3  # Advance by 3 months each epoch
    refit_soode_each_epoch: bool = True
    backfit_historical: bool = True  # 100% historical back-fit


@dataclass(frozen=True)
class StakingConfig:
    """SOODE-gated Kelly staking parameters."""
    kelly_fraction: float = 0.25
    min_edge_pct: float = 2.0  # Minimum SPE edge
    max_stake_pct: float = 0.03
    max_daily_exposure_pct: float = 0.10
    min_bankroll: float = 500.0
    drawdown_limit: float = 0.20


@dataclass(frozen=True)
class ParlayConfig:
    """Weaponized matrix parlay parameters."""
    min_spe: float = 76.0
    max_parlays_per_size: int = 3
    prob_floors: dict = field(default_factory=lambda: {
        2: 0.60, 3: 0.45, 4: 0.30, 5: 0.20, 6: 0.12
    })


@dataclass(frozen=True)
class AppConfig:
    soode: SOODEConfig = field(default_factory=SOODEConfig)
    wave: WaveCollapseConfig = field(default_factory=WaveCollapseConfig)
    wfo: WFOConfig = field(default_factory=WFOConfig)
    staking: StakingConfig = field(default_factory=StakingConfig)
    parlay: ParlayConfig = field(default_factory=ParlayConfig)
    model_version: str = "v2.1"

    # Five target markets
    market_types: tuple = ("h2h", "dc", "btts", "over_1.5", "over_2.5")

    # Target leagues
    target_leagues: tuple = (
        "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
        "Championship",
    )

    # Table A/B/C sizes
    table_a_rows: int = 10     # Recent odds profile per team
    table_b_rows: int = 60     # Multi-interval history (6 × 10)
    table_b_intervals: int = 6
    table_b_per_interval: int = 10

    # Telegram alerts
    telegram_bot_token: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", "")
    )


CONFIG = AppConfig()
