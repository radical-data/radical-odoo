"""DeepSeek provider — OpenAI-compatible implementation.

DeepSeek's API is OpenAI-compatible, so this provider inherits from
``OpenAIProvider`` and only overrides endpoint, models, and provider name.

Vision is not supported on the standard chat endpoint (DeepSeek-VL is
a separate model family).
"""

from .ai_provider_openai import OpenAIProvider

# Pricing per 1M tokens (USD) — updated 2025-09 (DeepSeek V3.2)
# Source: https://api-docs.deepseek.com/quick_start/pricing
DEEPSEEK_MODELS = {
    'deepseek-chat': {
        'name': 'DeepSeek Chat (fast, very affordable)',
        'input_price': 0.28,
        'output_price': 0.42,
    },
    'deepseek-reasoner': {
        'name': 'DeepSeek Reasoner (chain-of-thought)',
        'input_price': 0.28,
        'output_price': 0.42,
    },
}


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek provider — inherits OpenAI-compatible implementation.

    Only the endpoint, model list, vision support, and provider name differ.
    All request/response logic is inherited from ``OpenAIProvider``.
    """

    API_ENDPOINT = 'https://api.deepseek.com/chat/completions'
    MODELS = DEEPSEEK_MODELS
    VALIDATION_MODEL = 'deepseek-chat'

    def get_provider_name(self):
        return 'DeepSeek'

    def supports_vision(self):
        return False
