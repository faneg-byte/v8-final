---
name: hyperbolic-ai-patterns
description: Integration patterns for the Hyperbolic AI API including model selection, authentication, cost optimization, and usage in the V10/SPE pipeline. Use when calling Hyperbolic AI models, building LLM-powered features, or integrating open-source LLMs via API.
---

# Hyperbolic AI Patterns

## Quick Start

Hyperbolic AI provides an **OpenAI-compatible API** for open-source LLMs. Environment variable: `HAK`.

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["HAK"],
    base_url="https://api.hyperbolic.xyz/v1"
)

response = client.chat.completions.create(
    model="deepseek-ai/DeepSeek-V3-0324",
    messages=[{"role": "user", "content": "Your prompt here"}],
    max_tokens=4096,
    temperature=0.7
)
```

## Available Models (as of April 2026)

| Model | ID | Status | Best For |
|---|---|---|---|
| DeepSeek-V3-0324 | `deepseek-ai/DeepSeek-V3-0324` | Funded | General reasoning, analysis |
| DeepSeek-R1-0528 | `deepseek-ai/DeepSeek-R1-0528` | Funded | Deep reasoning, math |
| Qwen3-Coder-480B | `Qwen/Qwen3-Coder-480B-A35B-Instruct` | Funded | Code generation |
| Llama-3.3-70B | `meta-llama/Llama-3.3-70B-Instruct` | Funded | General, fast |
| DeepSeek-R1 | `deepseek-ai/DeepSeek-R1` | Funded | Deep reasoning |

**Default choice**: `DeepSeek-V3-0324` — best balance of capability and availability.

## Model Selection by Task

| Task | Model | Temperature | Max Tokens |
|---|---|---|---|
| News sentiment classification | DeepSeek-V3-0324 | 0.3 | 512 |
| Pick justification | DeepSeek-V3-0324 | 0.7 | 1024 |
| Feature engineering reasoning | DeepSeek-V3-0324 | 0.5 | 2048 |
| Parlay risk narrative | DeepSeek-V3-0324 | 0.7 | 1024 |
| Code generation | Qwen3-Coder-480B | 0.3 | 4096 |
| Complex math/stats | DeepSeek-R1-0528 | 0.3 | 4096 |

## Cost Optimization

- **Batch requests**: Combine multiple sentiment analyses into one prompt
- **Cache results**: Store LLM outputs in DB, don't re-process same news
- **Template prompts**: Use structured prompts that minimize output tokens
- **Use best model for task**: Credits are permanently funded; always use the optimal model for each task

## Error Handling

**Note:** Hyperbolic AI credits are permanently funded. No free-tier fallback logic is needed.

```python
import time

def call_hyperbolic(prompt, model="deepseek-ai/DeepSeek-V3-0324", retries=3):
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            if "429" in str(e):  # Rate limit
                time.sleep(2 ** attempt)
            elif str(e).startswith("5"):  # 5xx server error
                time.sleep(2 ** attempt)
            else:
                raise
    return None
```

## V10 Integration Points

1. **Ingestor**: News sentiment → `v10-llm-features` service
2. **Scanner**: Pick justifications → stored in `refined_alpha.llm_pick_justification`
3. **Weaponized Matrix**: Parlay narratives → stored in `weaponized_matrix.parlay_llm_narrative`
4. **Dashboard**: On-demand SOODE explanations

## Prompt Templates

Read `references/prompt_templates.md` for production-ready prompt templates for each integration point.
