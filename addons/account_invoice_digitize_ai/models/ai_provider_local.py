from .ai_provider import AIProvider


class LocalAIProvider(AIProvider):
    """Local / self-hosted AI provider — placeholder, not yet implemented.

    Planned: OpenAI-compatible API (Ollama, vLLM, LM Studio, llama.cpp).
    Configuration via ``ai_local_api_url``, ``ai_local_model_name``,
    ``ai_local_api_key``, ``ai_local_supports_vision`` in settings.
    """

    def extract(self, api_key, system_prompt, user_content, model):
        raise NotImplementedError('Local AI provider is not yet implemented.')

    def validate_api_key(self, api_key):
        return False, 'Local AI provider is not yet implemented. Coming soon!'

    def get_available_models(self):
        return []  # User-specified model name, no fixed list

    def estimate_cost(self, input_tokens, output_tokens, model):
        return 0.0  # Self-hosted = zero API cost

    def get_provider_name(self):
        return 'Local / Self-hosted'

    def supports_vision(self):
        return False  # Depends on deployed model, user-configurable
