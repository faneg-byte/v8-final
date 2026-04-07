# Product Requirements Document: V10 Soccer Prediction Engine

**Author:** Manus AI
**Date:** April 2026
**Project:** SPE (Sports Prediction Engine)

## 1. Executive Summary

The V10 Soccer Prediction Engine represents a paradigm shift from a monolithic prediction model to a hyper-specialized, ensemble-based architecture. Building upon the robust Cloud Run microservices foundation established in V8, V10 aims to push prediction accuracy to the theoretical limits of specific betting markets. While acknowledging that general 3-way match outcomes (Win/Draw/Loss) are bounded by irreducible stochastic uncertainty (~60-65% theoretical maximum), V10 targets **90-100% accuracy in lower-variance markets** such as Over 0.5 Goals and Double Chance [1] [2].

This evolution will be achieved within a strict $75/month infrastructure budget by leveraging free-tier data sources, cost-optimized Cloud Run deployments, and strategic integration of open-source Large Language Models (LLMs) via the Hyperbolic AI API.

## 2. Goals and Objectives

### 2.1 Primary Goals
- **Maximize Market-Specific Accuracy:** Achieve 90-100% accuracy in Double Chance and Over 0.5/1.5 Goals markets [1].
- **Enhance 3-Way Accuracy:** Push the 3-way (W/D/L) prediction accuracy to the theoretical ceiling of 60-65% through advanced feature engineering and ensemble modeling [1].
- **Maintain Cost Efficiency:** Keep total monthly infrastructure and API costs strictly under $75 [3].
- **Automate Value Identification:** Programmatically identify discrepancies between V10's calculated probabilities and bookmaker odds to flag value bets [1].

### 2.2 Success Metrics
| Metric | Current (V8) | Target (V10) |
|---|---|---|
| Overall Accuracy Range | 54-81% | 65-95%+ (Market Dependent) |
| Over 0.5 Goals Accuracy | N/A | 97-99% |
| Double Chance Accuracy | N/A | 90-94% |
| 3-Way (W/D/L) Accuracy | ~54% | 60-65% |
| Monthly Operating Cost | ~$22-25 | < $75 |

## 3. Core Prediction Methodology

V10 abandons the "one model fits all" approach in favor of a Bayesian Hierarchical framework combined with market-specific ensembles [2].

### 3.1 Mathematical Framework
The foundation of V10 is a Bayesian Hierarchical Logistic Regression model [2]. This framework is superior for quantifying uncertainty and modeling the inherent structure of soccer leagues:
- **Level 1 (Match):** Categorical distribution for match outcomes.
- **Level 2 (Team):** Normal distributions for offensive and defensive strengths (Pi-ratings).
- **Level 3 (League):** Hyperpriors to enable cross-league learning.

This approach ensures that predictions are not just point estimates but full posterior predictive distributions, allowing the system to identify bets where both probability and confidence are high [2].

### 3.2 Advanced Feature Engineering
V10 introduces several novel features designed to capture non-linear dynamics and latent team states:
- **Dynamic Pi-Ratings:** Continuous updating of team offensive and defensive ratings based on match outcomes, providing a more stable measure of true strength than simple win/loss records [1].
- **Expected Goals (xG) Integration:** Incorporating pre-shot and post-shot xG data to measure true offensive creation and defensive vulnerability, separating luck from sustainable performance [1].
- **Momentum-Weighted SOODE:** The Stochastic Oscillator of Offensive/Defensive Efficiency (SOODE) will be upgraded using an Exponentially Weighted Moving Average (EWMA) with an adaptive decay parameter. This allows the model to react faster to sudden changes in form (e.g., manager sackings, injury crises) [2].
- **Tactical Congruence Metric:** A non-linear feature measuring the stylistic matchup between teams (e.g., high-press vs. possession-based) to identify high-xG scenarios [2].

### 3.3 The "Drawishness" Solution
Draws represent a dynamic equilibrium and are notoriously difficult to predict [2]. V10 tackles this by modeling the *propensity for a draw* as a latent variable. Instead of predicting a draw directly, the system predicts the probability of a low-scoring match combined with the probability of a draw given a low-scoring context [2]. Furthermore, the system will heavily utilize the Double Chance market to sidestep the draw problem entirely when identifying high-confidence bets [2].

## 4. System Architecture

V10 extends the existing V8 Google Cloud Platform (GCP) architecture, adding specialized microservices while maintaining the cost-effective Cloud Run deployment strategy.

### 4.1 New Microservices
All new services will be deployed on Cloud Run (`e2-micro` instances) with minimum instances set to 0 to minimize costs [1].

1. **`pi-rating-service`:** Calculates and updates dynamic Pi-ratings for all tracked teams after every match [1].
2. **`xg-service`:** Ingests and processes raw event data to calculate advanced xG metrics [1].
3. **`timeseries-service`:** Manages LSTM and TimesNet models for analyzing team form and cyclical momentum patterns [1].
4. **`llm-feature-service`:** Interfaces with the Hyperbolic AI API to perform sentiment analysis on news and social media, generating quantitative "impact factors" for the prediction models [1].
5. **`market-modeler-service`:** Orchestrates training and inference for specific markets (O/U, BTTS, Double Chance) using tailored algorithms (e.g., Poisson regression for goals, XGBoost for classification) [1].
6. **`ensemble-service`:** Combines predictions from all base models using stacking or weighted averaging to produce the final probability distributions [1].

### 4.2 Database Enhancements
The existing Cloud SQL PostgreSQL instance (`v8-citadel`) will be expanded with new tables to support the advanced metrics:
- `pi_ratings`: Historical tracking of team offensive and defensive ratings.
- `xg_data`: Granular event-level data (shots, locations, pre/post-shot xG).
- `team_time_series_features`: Stored outputs from the LSTM models.
- `news_sentiment_data`: Processed LLM summaries and sentiment scores.
- `market_predictions`: The final ensemble predictions, including confidence intervals and value bet flags [1].

### 4.3 AI Integration (Hyperbolic API)
V10 leverages the Hyperbolic AI API (specifically `DeepSeek-V3-0324`) to process unstructured data and generate human-readable narratives [1] [4].
- **News Sentiment:** Analyzing injury reports and team news to create quantitative impact scores.
- **Pick Justification:** Generating 2-3 sentence explanations for high-confidence predictions based on the underlying statistical signals.
- **Parlay Risk Narrative:** Assessing the interdependency risk of multi-leg parlays generated in the Weaponized Matrix [4].

## 5. Walk-Forward Validation Protocol

To prevent overfitting and ensure the models adapt to the non-stationary nature of soccer, V10 will employ a strict Walk-Forward Optimization strategy [2].
- **Training Window:** 3 seasons of historical data.
- **Validation Window:** 4 weeks following the training window.
- **Test Window:** 1 week (out-of-sample prediction).
The window will roll forward weekly, continuously retraining the fast-adapting models (LSTM) and periodically recalibrating the slower foundational models (Pi-ratings) [2].

## 6. Budget and Cost Analysis

The V10 architecture is designed to operate well within the $75/month budget constraint.

| Component | Estimated Monthly Cost | Notes |
|---|---|---|
| Cloud SQL (db-f1-micro) | ~$9.00 | Existing V8 infrastructure |
| Cloud Run (Compute) | ~$5.00 | Assuming intermittent usage, scaling to 0 |
| Cloudflare Workers | $0.00 | Free tier sufficient for proxy |
| Hyperbolic AI API | ~$1.68 | Estimated based on DeepSeek-V3 pricing and projected token volume |
| Odds/Data APIs | ~$15.00 | Starter plans for odds and event data |
| **Total Estimated Cost** | **~$30.68** | Well under the $75 budget |

## 7. Implementation Roadmap

1. **Phase 1: Foundation (Weeks 1-2)**
   - Implement `pi-rating-service` and update database schema.
   - Refactor core model to Bayesian Hierarchical Logistic Regression.
2. **Phase 2: Market Specialization (Weeks 3-4)**
   - Develop `market-modeler-service` with specific models for Double Chance and O/U markets.
   - Implement the `ensemble-service`.
3. **Phase 3: AI and Unstructured Data (Weeks 5-6)**
   - Deploy `llm-feature-service` integrating the Hyperbolic API.
   - Implement web scraping for news and injury reports.
4. **Phase 4: Dashboard Integration and Testing (Weeks 7-8)**
   - Update the Flask dashboard to display market-specific predictions and LLM justifications.
   - Execute extensive walk-forward validation testing.

---

## References
[1] Gemini V10 Architecture Analysis. Local file: `gemini_v10_analysis.md`.
[2] Hyperbolic AI V10 Prediction Methodology Analysis. Local file: `hyperbolic_v10_analysis.md`.
[3] V8/SPE Project Reference. Local skill: `v8-spe-project`.
[4] Hyperbolic AI Patterns. Local skill: `hyperbolic-ai-patterns`.
