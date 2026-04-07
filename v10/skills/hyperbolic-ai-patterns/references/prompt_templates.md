# Hyperbolic AI Prompt Templates for V10

## News Sentiment Analysis

```
You are a sports analytics AI. Analyze the following news article about {team_name} and return a JSON object.

Article: {article_text}

Return ONLY valid JSON:
{
  "sentiment_score": <float -1.0 to 1.0>,
  "sentiment_magnitude": <float 0.0 to 1.0>,
  "impact_factor": <float 0.0 to 1.0>,
  "category": "<injury|transfer|form|tactical|morale|other>",
  "affected_players": ["<player_name>"],
  "summary": "<one sentence summary>"
}
```

## Pick Justification

```
You are a soccer analytics expert. Explain why this prediction is strong.

Match: {home_team} vs {away_team}
Market: {market_type}
Prediction: {prediction}
Model Confidence: {confidence}%

Key stats:
- Home Pi-Rating (attack/defense): {home_pi_att}/{home_pi_def}
- Away Pi-Rating (attack/defense): {away_pi_att}/{away_pi_def}
- Home xG last 5: {home_xg_5}
- Away xG last 5: {away_xg_5}
- Home SOODE: {home_soode}
- Away SOODE: {away_soode}
- H2H last 5: {h2h}

Write a 2-3 sentence justification focusing on the strongest statistical signals. Be specific with numbers. Do not hedge or use vague language.
```

## Parlay Risk Narrative

```
Analyze this parlay for interdependency risk and overall viability.

Parlay legs:
{legs_json}

For each pair of legs, assess:
1. Are the matches in the same league? (correlated conditions)
2. Do any teams share recent opponents? (form correlation)
3. Are the markets correlated? (e.g., two O/U bets in same league round)

Return a 3-4 sentence risk assessment with an overall risk rating: LOW / MEDIUM / HIGH.
```

## SOODE Shift Explanation

```
Explain this SOODE momentum shift for {team_name} in plain language.

SOODE 7 days ago: {soode_old}
SOODE now: {soode_new}
Change: {delta}
Recent results: {recent_results}
Recent xG: {recent_xg}

Write 2 sentences explaining what this shift means for upcoming matches. Reference specific results.
```
