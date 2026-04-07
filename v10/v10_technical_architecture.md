# V10 Technical Architecture & Implementation Specifications

**Author:** Manus AI
**Date:** April 2026
**Project:** SPE (Sports Prediction Engine)

## 1. System Overview

The V10 Soccer Prediction Engine builds upon the V8 Cloud Run microservices architecture. It transitions from a single monolithic prediction model to a distributed, ensemble-based system featuring specialized models for distinct betting markets. The architecture emphasizes modularity, leveraging independent services for feature engineering, model inference, and AI-driven narrative generation [1].

## 2. Infrastructure Stack

- **Compute:** Google Cloud Run (Serverless containers)
- **Database:** Google Cloud SQL (PostgreSQL 16)
- **Proxy/Gateway:** Cloudflare Workers (Workload Identity Federation for auth)
- **AI Inference:** Hyperbolic AI API (DeepSeek-V3-0324) [2]
- **Networking:** Serverless VPC Access Connector

## 3. Microservices Architecture

V10 introduces several new microservices to handle the increased complexity of feature engineering and market-specific modeling [1].

### 3.1 Data Ingestion & Processing
- **`ingestor`:** Expanded to handle event-level data (xG, shot locations) from sources like Understat and FBref, as well as weather and news data.
- **`pi-rating-service`:** Calculates dynamic offensive and defensive ratings for all teams. Triggered by the `ingestor` upon match completion.
- **`xg-service`:** Processes raw event data to generate pre-shot and post-shot Expected Goals (xG) metrics.

### 3.2 Modeling & Inference
- **`timeseries-service`:** Manages LSTM and TimesNet models to capture team form, momentum, and cyclical patterns over time.
- **`wdl-modeler`:** The evolution of the V8 `modeler`, now strictly focused on 3-way Win/Draw/Loss and binary W/L predictions using XGBoost/LightGBM.
- **`market-modeler-service`:** Orchestrates specialized models for specific markets:
  - Over/Under Goals (Poisson/Negative Binomial Regression)
  - Both Teams to Score (Logistic Regression/XGBoost)
  - Double Chance (XGBoost/CatBoost)
- **`ensemble-service`:** The final aggregation layer that combines predictions from the `wdl-modeler` and `market-modeler-service` using stacking or weighted averaging to produce final probability distributions.

### 3.3 AI & Narrative Generation
- **`llm-feature-service`:** Interfaces with the Hyperbolic AI API to process unstructured text (news, social media). It generates quantitative sentiment scores and "impact factors" for the modeling pipeline, as well as human-readable pick justifications and parlay risk narratives [2].

## 4. Database Schema Extensions

The existing PostgreSQL database (`v8-citadel`) will be extended to support the new data structures [1].

```sql
-- Dynamic Pi-Ratings
CREATE TABLE pi_ratings (
    team_id INT NOT NULL,
    league_id INT NOT NULL,
    date DATE NOT NULL,
    home_attack_rating REAL NOT NULL,
    home_defense_rating REAL NOT NULL,
    away_attack_rating REAL NOT NULL,
    away_defense_rating REAL NOT NULL,
    learning_rate REAL DEFAULT 0.05,
    PRIMARY KEY (team_id, league_id, date)
);

-- Event-level Expected Goals
CREATE TABLE xg_data (
    match_id INT NOT NULL,
    team_id INT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_minute INT,
    x_coord REAL,
    y_coord REAL,
    pre_shot_xg REAL,
    is_goal BOOLEAN
);

-- Time-Series Features (LSTM Outputs)
CREATE TABLE team_time_series_features (
    feature_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    team_id INT NOT NULL,
    match_id INT NOT NULL,
    feature_name VARCHAR(100) NOT NULL,
    feature_value REAL NOT NULL,
    prediction_date DATE NOT NULL
);

-- LLM Processed Sentiment
CREATE TABLE news_sentiment_data (
    item_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source_url TEXT,
    publish_date TIMESTAMP,
    sentiment_score REAL,
    inferred_impact_factor REAL,
    related_team_id INT
);

-- Final Market Predictions
CREATE TABLE market_predictions (
    prediction_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    match_id INT NOT NULL,
    market_type VARCHAR(50) NOT NULL,
    predicted_outcome VARCHAR(100) NOT NULL,
    probability REAL NOT NULL,
    model_confidence REAL NOT NULL,
    value_bet_flag BOOLEAN DEFAULT FALSE,
    llm_justification TEXT
);
```

## 5. Walk-Forward Validation Protocol

To ensure models adapt to the non-stationary nature of soccer and prevent overfitting, V10 enforces a strict Walk-Forward Optimization strategy [3].

1. **Training Window:** 3 seasons of historical data.
2. **Validation Window:** 4 weeks following the training window.
3. **Test Window:** 1 week (out-of-sample prediction).

The pipeline rolls forward weekly. Fast-adapting models (like the LSTM form models) are retrained weekly, while slower foundational models (Pi-ratings, Bayesian Hierarchical) are recalibrated monthly or quarterly [3].

## 6. Hyperbolic AI Integration

V10 relies on the Hyperbolic AI API (`DeepSeek-V3-0324`) for advanced reasoning and feature extraction. Guaranteed API credits eliminate the need for fallback logic to free-tier models [2].

- **News Sentiment:** `llm-feature-service` analyzes team news to generate numerical impact scores for the models.
- **Pick Justification:** Generates concise explanations for high-confidence predictions based on statistical signals.
- **Parlay Risk:** Assesses interdependency risks in multi-leg parlays generated by the Weaponized Matrix.

---

## References
[1] Gemini V10 Architecture Analysis. Local file: `gemini_v10_analysis.md`.
[2] Hyperbolic AI Patterns. Local skill: `hyperbolic-ai-patterns`.
[3] Hyperbolic AI V10 Prediction Methodology Analysis. Local file: `hyperbolic_v10_analysis.md`.
