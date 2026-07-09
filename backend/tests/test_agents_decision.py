"""Decision Node 关键测试——basis_type 归并规则（最大不确定性优先）+ 字段级 model_versions_json。"""
import pytest

from agents.nodes.decision import merge_basis_type, build_decision_card


def test_merge_basis_type_max_uncertainty_wins():
    """归并规则：llm_reasoning > hybrid > model_fallback > model。"""
    assert merge_basis_type(["model", "model", "model"]) == "model"
    assert merge_basis_type(["model", "model_fallback"]) == "model_fallback"
    assert merge_basis_type(["model", "hybrid"]) == "hybrid"
    assert merge_basis_type(["model", "llm_reasoning"]) == "llm_reasoning"
    assert merge_basis_type(["model_fallback", "hybrid", "llm_reasoning"]) == "llm_reasoning"


def test_merge_basis_type_empty_returns_model():
    assert merge_basis_type([]) == "model"


def test_build_decision_card_basic():
    """合并工具结果为决策卡：字段级 model_versions_json + cadence 数组。"""
    tool_results = {
        "target": {  # 来自 forward_pe_target
            "tool": "forward_pe_target", "basis_type": "model",
            "model_version": "forward_pe_target.v1",
            "outputs": {"target_price": 1900.0, "current_price": 1685.0},
            "citations": [{"source": "astock.full_valuation", "code": "600519"}],
        },
        "stop": {  # 来自 atr_stop（fallback）
            "tool": "atr_stop", "basis_type": "model_fallback",
            "model_version": "atr_stop.v1",
            "outputs": {"stop_price": 1550.2, "current_price": 1685.0, "fallback_reason": "no_kline"},
        },
        "entry": {  # 来自 pe_percentile_revert
            "tool": "pe_percentile_revert", "basis_type": "model",
            "model_version": "pe_percentile_revert.v1",
            "outputs": {"target_price": 1900.0, "current_price": 1685.0},
        },
        "position": {  # 来自 risk_based_position
            "tool": "risk_based_position", "basis_type": "model",
            "model_version": "risk_based_position.v1",
            "outputs": {"shares": 125.0, "position_pct_of_equity": 0.21},
        },
    }
    card = build_decision_card(
        code="600519", name="贵州茅台",
        current_price=1685.0,
        target_price=1900.0, entry_low=1685.0, entry_high=1720.0,
        stop_loss=1550.2, take_profit=2080.0,
        cadence=[
            {"batch": 1, "pct": 0.40, "trigger": "immediate", "price": 1685.0},
            {"batch": 2, "pct": 0.30, "trigger": "pullback_to:1650", "price": 1650.0},
            {"batch": 3, "pct": 0.30, "trigger": "breakout_above:1780", "price": 1780.0},
        ],
        tool_results=tool_results,
        explanation="基于 forward PE 目标价 1900 + ATR fallback 止损 1550",
    )
    # 整卡 basis_type = model_fallback（含一个 model_fallback 字段）
    assert card["basis_type"] == "model_fallback"
    # 字段级 model_versions_json
    mv = card["model_versions_json"]
    assert "target_price" in mv and "forward_pe_target.v1" in mv["target_price"]
    assert "stop_loss" in mv and "atr_stop.v1" in mv["stop_loss"]
    assert "model_fallback" in mv["stop_loss"] or "fallback" in mv["stop_loss"].lower()
    # cadence 是数组
    assert isinstance(card["cadence"], list) and len(card["cadence"]) == 3
    # citations 来自所有工具
    assert len(card["citations"]) >= 1
    # code / name / current_price
    assert card["code"] == "600519"
    assert card["name"] == "贵州茅台"
    assert card["current_price"] == 1685.0


@pytest.mark.asyncio
async def test_decision_node_returns_none_when_all_tools_fail(monkeypatch):
    """所有工具失败（current_price=0）→ 不推半成品决策卡。"""
    from agents.nodes.decision import decision_node

    async def fail_invoke(tool, **kwargs):
        return (
            {
                "tool": tool.name, "error": "mock failure",
                "basis_type": "model_fallback",
                "model_version": f"{tool.name}.v1",
                "outputs": {"fallback_reason": "tool_error"},
                "citations": [], "model_assumptions": [],
            },
            {"tool": tool.name, "status": "error", "args": kwargs,
             "summary": "mock failure"},
        )

    # StructuredTool 是 Pydantic model，不能 setattr；直接 mock _invoke（graph ↔ tool 的接缝）
    monkeypatch.setattr("agents.nodes.decision._invoke", fail_invoke)

    async def fake_lookup(code):
        return code
    monkeypatch.setattr("agents.nodes.decision._lookup_name", fake_lookup)

    state = {
        "messages": [{"role": "user", "content": "分析 600519"}],
        "context_codes": ["600519"],
    }
    result = await decision_node(state)
    assert result["decision_card"] is None
    assert result["intent"] == "decision_failed"


@pytest.mark.asyncio
async def test_decision_node_collects_tool_traces(monkeypatch):
    """decision_node 应把每次工具调用的 tool/status/args 收集到 state['tool_traces']。"""
    from agents.nodes.decision import decision_node

    # 模拟 _invoke 返回 (result, trace) 元组——Task 3 改完 _invoke 才会这样返回
    # 这个测试在改实现之前会 FAIL（_invoke 当前只返回 result dict）
    async def fake_target_invoke(tool, **kwargs):
        return (
            {
                "tool": "forward_pe_target", "basis_type": "model",
                "model_version": "forward_pe_target.v1",
                "outputs": {"target_price": 1900.0, "current_price": 1685.0},
                "citations": [], "model_assumptions": [],
            },
            {"tool": "forward_pe_target", "status": "ok", "args": kwargs, "summary": "目标价 1900.0"},
        )

    async def fake_stop_invoke(tool, **kwargs):
        return (
            {
                "tool": "atr_stop", "basis_type": "model",
                "model_version": "atr_stop.v1",
                "outputs": {"stop_price": 1550.0, "current_price": 1685.0},
                "citations": [], "model_assumptions": [],
            },
            {"tool": "atr_stop", "status": "ok", "args": kwargs, "summary": "止损 1550.0"},
        )

    async def fake_other_invoke(tool, **kwargs):
        return (
            {
                "tool": tool.name, "basis_type": "model_fallback",
                "model_version": f"{tool.name}.v1",
                "outputs": {},
                "citations": [], "model_assumptions": [],
            },
            {"tool": tool.name, "status": "ok", "args": kwargs},
        )

    # 顺序：decision_node 调 _invoke 的顺序是 target → stop → entry → pos → cad
    invoke_mocks = [fake_target_invoke, fake_stop_invoke, fake_other_invoke,
                    fake_other_invoke, fake_other_invoke]
    call_idx = {"i": 0}

    async def mock_invoke(tool, **kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        if i < len(invoke_mocks):
            return await invoke_mocks[i](tool, **kwargs)
        return await fake_other_invoke(tool, **kwargs)

    # mock _invoke 而非 tool.ainvoke（StructuredTool 是 Pydantic model 不让 set attr，Task 2 已验证）
    monkeypatch.setattr("agents.nodes.decision._invoke", mock_invoke)

    async def fake_lookup(code):
        return "测试"
    monkeypatch.setattr("agents.nodes.decision._lookup_name", fake_lookup)

    state = {
        "messages": [{"role": "user", "content": "分析 600519"}],
        "context_codes": ["600519"],
    }
    result = await decision_node(state)
    traces = result.get("tool_traces") or []
    assert isinstance(traces, list)
    assert len(traces) == 5, f"期望 5 条 trace，实际 {len(traces)}"
    tools_called = [t["tool"] for t in traces]
    assert "forward_pe_target" in tools_called
    assert "atr_stop" in tools_called
    for t in traces:
        assert t["status"] in ("ok", "error")
        assert "args" in t
