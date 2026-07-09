"""定量工具层（纯 Python 函数，无 LLM）。

每个函数返回统一 contract：
{
    "tool": "atr_stop",
    "inputs": {...},
    "outputs": {...},
    "basis_type": "model" | "model_fallback" | "llm_reasoning" | "hybrid",
    "model_version": "atr_stop.v1",
    "model_assumptions": ["..."],
    "citations": [{"source": "astock.kline", "code": "...", "range": "..."}],
    "explanation": "..."
}

数据源单一：只调 astock.py / gstock.py；不直接打 HTTP。
"""
