# Accuracy Research Summary

## Key Papers

### Hubacek et al. (2023) — "Evaluation of Soccer Prediction Models" (arXiv:2309.14807)
- Comprehensive evaluation of deep learning vs traditional ML for soccer prediction
- **TimesNet + Transformer** outperformed all 2017 Soccer Prediction Challenge models
- CatBoost + pi-ratings was best performing in 2023 challenge
- Deep learning models showed +2-3% improvement over gradient boosting when given sufficient data
- Feature importance: pi-ratings > Elo > bookmaker odds > recent form

### Constantinou (2013) — "Pi-ratings"
- Dynamic rating decomposing team strength into 4 components: home attack, home defense, away attack, away defense
- Updates after every match using learning rates (default 0.05)
- Consistently the single most predictive feature across studies

### Dixon & Coles (1997) — Bivariate Poisson
- Foundation for goal-based prediction models
- Models home/away goals as correlated Poisson processes
- Still competitive baseline after 25+ years

### Graph Neural Networks for Passing Networks (2025)
- Model team playing style as graph (nodes=players, edges=passes)
- 5-20% improvement over baseline using pass network features
- Requires event-level data (expensive)

## Theoretical Accuracy Limits

Soccer has irreducible stochastic uncertainty:
- Random events (deflections, referee errors, injuries during match)
- Draw outcomes represent equilibrium states — fundamentally unpredictable
- Even bookmakers with billions of data points: ~55% 3-way accuracy
- **Market-specific models bypass this** by collapsing outcome space

## What Actually Improves Accuracy

| Technique | Typical Improvement | Evidence Level |
|---|---|---|
| Pi-ratings over Elo | +3-5% | Strong (multiple studies) |
| xG integration | +2-4% | Strong |
| Ensemble stacking | +2-3% | Strong |
| LSTM time-series | +1-3% | Moderate |
| Market specialization | +5-15% on target market | Strong |
| Sentiment analysis | +1-2% | Weak-Moderate |
| Weather features | +0.5-1% on O/U | Weak |
