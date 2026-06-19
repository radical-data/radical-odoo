"""Mistral AI provider — OpenAI-compatible implementation.

Mistral's API is OpenAI-compatible, so this provider inherits from
``OpenAIProvider`` and only overrides endpoint, models, and provider name.
"""

from .ai_provider_openai import OpenAIProvider

# Pricing per 1M tokens (USD) — updated 2025-12
# Source: https://mistral.ai/pricing
MISTRAL_MODELS = {
    'mistral-small-3-2-25-06': {
        'name': 'Mistral Small 3.2 (fast, affordable)',
        'input_price': 0.06,
        'output_price': 0.18,
    },
    'mistral-medium-3-1-25-08': {
        'name': 'Mistral Medium 3.1 (balanced)',
        'input_price': 0.40,
        'output_price': 2.00,
    },
    'mistral-large-3-25-12': {
        'name': 'Mistral Large 3 (best quality)',
        'input_price': 0.50,
        'output_price': 1.50,
    },
}


class MistralProvider(OpenAIProvider):
    """Mistral AI provider — inherits OpenAI-compatible implementation.

    Only the endpoint, model list, and provider name differ.
    All request/response logic is inherited from ``OpenAIProvider``.
    """

    API_ENDPOINT = 'https://api.mistral.ai/v1/chat/completions'
    MODELS = MISTRAL_MODELS
    VALIDATION_MODEL = 'mistral-small-3-2-25-06'

    def get_provider_name(self):
        return 'Mistral AI'
