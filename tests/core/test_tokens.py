from context_garden.core.tokens import TokenUsage, estimate_tokens


def test_estimate_tokens_empty_string():
    assert estimate_tokens("") == 0


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)


def test_token_usage_total():
    usage = TokenUsage(
        input_tokens=100,
        repository_tokens=200,
        tool_result_tokens=50,
        skill_tokens=25,
        output_tokens=10,
    )
    assert usage.total_tokens == 385


def test_token_usage_defaults_to_zero():
    assert TokenUsage().total_tokens == 0
