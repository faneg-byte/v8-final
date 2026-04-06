-- V8 Engine Schema v3.0
-- Changes from v2.1:
--   Table C: added weather_temp_c, weather_rain_mm, weather_wind_kph, cards_yellow_accum, cards_red_recent
--   matches: added home_yellows, away_yellows, home_reds, away_reds for card tracking
--   model_predictions: new table storing per-match model probabilities for SOODE consumption
-- PROPRIETARY
BEGIN;

CREATE TABLE IF NOT EXISTS teams (
    team_id     SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    country     TEXT,
    league      TEXT,
    aliases     TEXT[],
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS matches (
    match_id      TEXT PRIMARY KEY,
    home_id       INT REFERENCES teams(team_id),
    away_id       INT REFERENCES teams(team_id),
    match_date    TIMESTAMPTZ NOT NULL,
    league        TEXT NOT NULL,
    season        TEXT,
    home_goals    INT,
    away_goals    INT,
    home_yellows  INT DEFAULT 0,
    away_yellows  INT DEFAULT 0,
    home_reds     INT DEFAULT 0,
    away_reds     INT DEFAULT 0,
    status        TEXT DEFAULT 'scheduled',
    source        TEXT,
    ingested_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(home_id, away_id, match_date)
);
CREATE INDEX IF NOT EXISTS idx_m_date ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_m_league ON matches(league, match_date);
CREATE INDEX IF NOT EXISTS idx_m_status ON matches(status);

-- Model predictions (feeds SOODE divergence computation)
CREATE TABLE IF NOT EXISTS model_predictions (
    id              SERIAL PRIMARY KEY,
    match_id        TEXT REFERENCES matches(match_id),
    team_id         INT REFERENCES teams(team_id),
    market_type     TEXT NOT NULL,
    predicted_prob  NUMERIC(6,4) NOT NULL,
    actual_outcome  TEXT,
    was_correct     BOOLEAN,
    model_version   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(match_id, team_id, market_type, model_version)
);
CREATE INDEX IF NOT EXISTS idx_mp_team ON model_predictions(team_id, created_at);

-- Table A: per-team odds profile (10 rows)
CREATE TABLE IF NOT EXISTS team_odds_profile (
    id              SERIAL PRIMARY KEY,
    team_id         INT REFERENCES teams(team_id),
    match_id        TEXT REFERENCES matches(match_id),
    match_date      TIMESTAMPTZ,
    market_type     TEXT NOT NULL,
    opening_odds    NUMERIC(6,3),
    closing_odds    NUMERIC(6,3),
    predicted_outcome TEXT,
    actual_outcome  TEXT,
    odds_movement   NUMERIC(6,3),
    result_flag     BOOLEAN,
    row_rank        INT,
    UNIQUE(team_id, match_id, market_type)
);

-- Table B: per-team multi-interval history (60 rows, 6x10)
CREATE TABLE IF NOT EXISTS team_match_intervals (
    id            SERIAL PRIMARY KEY,
    team_id       INT REFERENCES teams(team_id),
    match_id      TEXT REFERENCES matches(match_id),
    interval_id   INT NOT NULL CHECK (interval_id BETWEEN 1 AND 6),
    match_date    TIMESTAMPTZ,
    opponent_id   INT REFERENCES teams(team_id),
    venue         TEXT CHECK (venue IN ('home', 'away')),
    goals_for     INT,
    goals_against INT,
    result        TEXT CHECK (result IN ('W', 'D', 'L')),
    row_rank      INT,
    UNIQUE(team_id, match_id)
);

-- Table C: per-team pre-match context (revised with weather + cards)
CREATE TABLE IF NOT EXISTS team_match_context (
    id                    SERIAL PRIMARY KEY,
    team_id               INT REFERENCES teams(team_id),
    match_id              TEXT REFERENCES matches(match_id),
    rest_days             INT,
    weather_temp_c        NUMERIC(5,1),
    weather_rain_mm       NUMERIC(5,1),
    weather_wind_kph      NUMERIC(5,1),
    cards_yellow_accum    INT DEFAULT 0,
    cards_red_recent      INT DEFAULT 0,
    manager_change_flag   BOOLEAN DEFAULT FALSE,
    injured_players       TEXT[],
    suspended_players     TEXT[],
    rivalry_flag          BOOLEAN DEFAULT FALSE,
    news_sentiment        NUMERIC(5,3) DEFAULT 0,
    travel_distance_km    NUMERIC(8,1),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, match_id)
);

-- Odds history
CREATE TABLE IF NOT EXISTS odds_history (
    id          SERIAL PRIMARY KEY,
    match_id    TEXT REFERENCES matches(match_id),
    bookmaker   TEXT,
    market_type TEXT NOT NULL,
    selection   TEXT NOT NULL,
    odds        NUMERIC(6,3) NOT NULL,
    is_opening  BOOLEAN DEFAULT FALSE,
    captured_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_oh ON odds_history(match_id, market_type);

-- SOODE keys
CREATE TABLE IF NOT EXISTS soode_keys (
    id                SERIAL PRIMARY KEY,
    team_id           INT REFERENCES teams(team_id),
    micro_grip        NUMERIC(6,4) NOT NULL,
    meso_grip         NUMERIC(6,4) NOT NULL,
    macro_grip        NUMERIC(6,4) NOT NULL,
    dna_grip          NUMERIC(6,4) NOT NULL,
    system_diagnosis  TEXT NOT NULL,
    bootstrap_mode    BOOLEAN DEFAULT FALSE,
    model_version     TEXT,
    computed_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(team_id, model_version)
);

-- Live Alpha
CREATE TABLE IF NOT EXISTS live_alpha (
    id                SERIAL PRIMARY KEY,
    match_id          TEXT REFERENCES matches(match_id),
    match_date        TIMESTAMPTZ,
    home_team         TEXT,
    away_team         TEXT,
    league            TEXT,
    market_type       TEXT NOT NULL,
    predicted_outcome TEXT NOT NULL,
    spe_implied_prob  NUMERIC(6,2) NOT NULL,
    model_version     TEXT,
    channel_weights   JSONB,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_la ON live_alpha(match_date);

-- Refined Alpha
CREATE TABLE IF NOT EXISTS refined_alpha (
    id                  SERIAL PRIMARY KEY,
    alpha_id            INT REFERENCES live_alpha(id),
    match_id            TEXT REFERENCES matches(match_id),
    home_diagnosis      TEXT,
    away_diagnosis      TEXT,
    matchup_class       TEXT,
    kelly_modifier      NUMERIC(4,2),
    accentuation_flag   TEXT,
    refined_spe         NUMERIC(6,2),
    recommended_action  TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Weaponized Matrix
CREATE TABLE IF NOT EXISTS weaponized_matrix (
    id                  SERIAL PRIMARY KEY,
    parlay_id           TEXT NOT NULL,
    leg_number          INT NOT NULL,
    alpha_id            INT REFERENCES live_alpha(id),
    match_id            TEXT REFERENCES matches(match_id),
    market_type         TEXT NOT NULL,
    selection           TEXT NOT NULL,
    spe_implied_prob    NUMERIC(6,2),
    raw_cumulative      NUMERIC(6,2),
    adjusted_cumulative NUMERIC(6,2),
    correlation_penalty NUMERIC(6,4),
    risk_grade          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wm ON weaponized_matrix(parlay_id);

-- Bet log
CREATE TABLE IF NOT EXISTS bet_log (
    id            SERIAL PRIMARY KEY,
    signal_source TEXT NOT NULL,
    signal_id     INT,
    parlay_id     TEXT,
    stake         NUMERIC(8,2) NOT NULL,
    odds_taken    NUMERIC(6,3) NOT NULL,
    result        TEXT,
    pnl           NUMERIC(10,2),
    settled_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Audit trail
CREATE TABLE IF NOT EXISTS audit_trail (
    id          SERIAL PRIMARY KEY,
    service     TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit ON audit_trail(created_at DESC);

-- WFO calibration
CREATE TABLE IF NOT EXISTS wfo_calibration (
    id              SERIAL PRIMARY KEY,
    wfo_epoch       INT NOT NULL,
    train_start     DATE NOT NULL,
    train_end       DATE NOT NULL,
    test_start      DATE NOT NULL,
    test_end        DATE NOT NULL,
    channel_weights JSONB NOT NULL,
    log_loss        NUMERIC(8,6),
    accuracy        NUMERIC(5,4),
    soode_thresholds JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Views
CREATE OR REPLACE VIEW v_live_alpha_enriched AS
SELECT la.*, ra.matchup_class, ra.kelly_modifier, ra.accentuation_flag, ra.refined_spe,
       ra.recommended_action
FROM live_alpha la
LEFT JOIN refined_alpha ra ON la.id = ra.alpha_id
ORDER BY la.match_date, la.spe_implied_prob DESC;

CREATE OR REPLACE VIEW v_bankroll AS
SELECT
    COALESCE(SUM(pnl) FILTER (WHERE result IS NOT NULL), 0) AS realized_pnl,
    COALESCE(SUM(stake) FILTER (WHERE result IS NULL), 0) AS open_exposure,
    COUNT(*) FILTER (WHERE result = 'win') AS wins,
    COUNT(*) FILTER (WHERE result = 'loss') AS losses,
    COUNT(*) FILTER (WHERE result IS NOT NULL) AS total_settled
FROM bet_log;

COMMIT;
