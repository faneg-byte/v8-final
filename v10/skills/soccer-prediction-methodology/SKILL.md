---
name: soccer-prediction-methodology
description: Research-backed soccer prediction methodology covering accuracy benchmarks, model architectures, feature engineering, and market-specific strategies. Use when building, improving, or evaluating soccer/football prediction models, designing ML pipelines for sports betting, or analyzing prediction accuracy.
---

# Soccer Prediction Methodology

Research-backed reference for soccer prediction systems, synthesized from academic literature (2017-2026), the V8/V10 SPE project, and AI consultations with Gemini and DeepSeek-V3.

## Accuracy Benchmarks (Realistic Targets)

| Market | Achievable | Theoretical Ceiling | Notes |
|---|---|---|---|
| Double Chance (1X, X2) | 90-95% | ~96% | Binary, eliminates draw |
| Over/Under 0.5 | 92-97% | ~98% | Nearly all matches score |
| Over/Under 1.5 | 88-93% | ~95% | Strong xG correlation |
| Over/Under 2.5 | 78-85% | ~88% | Well-studied |
| BTTS | 75-83% | ~85% | Defensive metrics + xG |
| Binary W/L (no draw) | 80-88% | ~90% | Removes hardest class |
| 3-Way WDL | 62-68% | ~65% | Irreducible draw uncertainty |

**Key insight**: 90-100% is achievable on specific markets (DC, O/U 0.5/1.5), NOT on 3-way WDL.

## Model Stack (Proven Architecture)

### Foundation Layer
- **XGBoost/LightGBM/CatBoost**: Gradient boosting for tabular features. CatBoost best for categorical.
- **Bayesian Hierarchical Logistic Regression** (PyMC/Stan): Uncertainty quantification, cross-league learning.

### Specialized Layer
- **Poisson/Negative Binomial**: Goal count modeling for O/U markets. Use xG as offset.
- **LSTM Networks**: Time-series team form. Rolling 10-match windows.
- **GARCH**: Performance volatility. Flags inconsistent teams.
- **Beta Regression**: Draw propensity modeling.

### Ensemble Layer
- **Stacking**: Meta-learner trained on base model predictions.
- **Walk-Forward Validation**: Train to week `t`, validate `t+1`, predict `t+2`. Never look ahead.

## Critical Features (Ranked by Impact)

1. **Pi-Ratings**: Dynamic home/away attack/defense ratings (Constantinou 2013). Best single feature.
2. **Expected Goals (xG)**: Pre-shot xG from Understat/FBref. pdxG for finishing quality.
3. **SOODE**: Stochastic Oscillator of Offensive/Defensive Efficiency. Proprietary momentum.
4. **Bookmaker Implied Probabilities**: Gold standard baseline. Always include.
5. **EWMA Form**: Exponentially weighted moving average with adaptive decay.
6. **LSTM Form Score**: Deep learning time-series for momentum/streaks.
7. **Sentiment/News**: LLM-processed injury/morale signals. +1-2% edge.
8. **Weather**: Temperature, wind, precipitation. Impact on O/U markets.

## The Draw Problem

Draws are the hardest prediction. Novel approach:
1. Model "drawishness" as latent variable: style similarity + defensive parity + low offensive efficiency
2. Conditional: `P(Draw) ≈ P(Goals < 2.5) × P(Draw | Goals < 2.5)`
3. Accept ~40% ceiling; redirect to Double Chance market.

## Calibration Over Accuracy

Optimize for **Log Loss** and **Brier Score**, not raw accuracy. Betting rule: only recommend when `P > 0.70 AND σ < 0.10`.

## Free Data Sources

| Source | Data |
|---|---|
| Understat.com | xG, shot data, player stats |
| FBref.com | Match stats, passing networks |
| OpenWeatherMap (free tier) | Weather at match locations |
| NewsAPI.org (free tier) | Sports news headlines |

## Resources

Read `references/accuracy_research.md` for detailed academic findings and citations.
Read `references/feature_engineering.md` for implementation details of each feature.
