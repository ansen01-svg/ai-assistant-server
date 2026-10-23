from decimal import Decimal

# USD per 1M tokens: (input, output). Update when Anthropic changes prices.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal(4), Decimal(20)),
    "claude-sonnet-5-5": (Decimal(2), Decimal(10)),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),  # prompts up to 100K tokens
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    prices = PRICES.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
