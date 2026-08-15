from agent.runtime import PRODUCT_TRANSITION_LIMIT


def test_transition_limit_is_not_advertised_when_cross_resume_is_unproven():
    # 实测（langgraph 1.2.11）：recursion_limit 按单次 invoke 计，不跨 resume 累计；
    # 在没有可靠的累计上限证据前，1A 不宣称任何转移数策略。
    assert PRODUCT_TRANSITION_LIMIT is None
