# Feature Engineering Implementation Guide

## Pi-Ratings

```python
# Pi-Rating update after match result
def update_pi_rating(team, opponent, goals_for, goals_against, is_home, lr=0.05):
    expected = 1 / (1 + 10**((opponent.defense - team.attack) / 400))
    actual = goals_for / max(goals_for + goals_against, 1)
    
    if is_home:
        team.home_attack += lr * (actual - expected)
        opponent.away_defense += lr * (expected - actual)
    else:
        team.away_attack += lr * (actual - expected)
        opponent.home_defense += lr * (expected - actual)
```

Initialize all ratings at 0. Learning rate 0.05 is standard; increase to 0.08 for newly promoted teams.

## Expected Goals (xG)

Scrape from Understat.com (free, covers top 6 European leagues):
```python
import requests
import json

def get_match_xg(match_id):
    url = f"https://understat.com/match/{match_id}"
    resp = requests.get(url)
    # Parse embedded JSON from page source
    data = json.loads(resp.text.split("shotsData")[1].split("=")[1].split("</script>")[0].strip().rstrip(";"))
    return data
```

Key xG features:
- `xg_for`: Total xG created by team
- `xg_against`: Total xG conceded
- `xg_diff`: xg_for - xg_against (most predictive single xG feature)
- `xg_overperformance`: actual_goals - xg_for (regression signal — positive = lucky)

## SOODE (Stochastic Oscillator of Offensive/Defensive Efficiency)

V2 enhancement: incorporate xG into SOODE calculation:
```python
def soode_v2(team_stats, window=14):
    off_eff = (team_stats['goals'] + team_stats['xg_for']) / 2
    def_eff = (team_stats['goals_against'] + team_stats['xg_against']) / 2
    
    off_high = off_eff.rolling(window).max()
    off_low = off_eff.rolling(window).min()
    def_high = def_eff.rolling(window).max()
    def_low = def_eff.rolling(window).min()
    
    off_soode = 100 * (off_eff - off_low) / (off_high - off_low + 1e-8)
    def_soode = 100 * (def_eff - def_low) / (def_high - def_low + 1e-8)
    
    return off_soode, def_soode
```

## EWMA with Adaptive Decay

```python
def adaptive_ewma(series, base_halflife=5):
    volatility = series.rolling(10).std()
    # High volatility = shorter halflife (more responsive)
    halflife = base_halflife / (1 + volatility)
    return series.ewm(halflife=halflife).mean()
```

## Walk-Forward Validation Protocol

```python
def walk_forward_validate(data, model, train_weeks=52, val_weeks=4):
    results = []
    for t in range(train_weeks, len(data) - val_weeks, val_weeks):
        train = data[:t]
        val = data[t:t+val_weeks]
        model.fit(train)
        preds = model.predict(val)
        results.append(evaluate(preds, val))
    return aggregate(results)
```
