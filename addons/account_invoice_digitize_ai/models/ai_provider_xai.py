"""xAI (Grok) provider — OpenAI-compatible implementation.

xAI's API is 100% OpenAI-compatible, so this provider inherits from
``OpenAIProvider`` and only overrides endpoint, models, and provider name.
"""

from .ai_provider_openai import OpenAIProvider

# Pricing per 1M tokens (USD) — updated 2025-06
# Source: https://docs.x.ai/developers/models
XAI_MODELS = {
    'grok-3': {
        'name': 'Grok 3 (advanced reasoning)',
        'input_price': 3.00,
        'output_price': 15.00,
    },
    'grok-3-mini': {
        'name': 'Grok 3 Mini (fast, affordable)',
        'input_price': 0.20,
        'output_price': 0.50,
    },
    'grok-2': {
        'name': 'Grok 2 (vision, balanced)',
        'input_price': 2.00,
        'output_price': 5.00,
    },
}


class XAIProvider(OpenAIProvider):
    """xAI (Grok) provider — inherits OpenAI-compatible implementation.

    Only the endpoint, model list, and provider name differ.
    All request/response logic is inherited from ``OpenAIProvider``.
    """

    API_ENDPOINT = 'https://api.x.ai/v1/chat/completions'
    MODELS = XAI_MODELS
    VALIDATION_MODEL = 'grok-3-mini'

    def get_provider_name(self):
        return 'xAI (Grok)'
