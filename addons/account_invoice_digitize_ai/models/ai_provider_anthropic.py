import json
import logging

from .ai_provider import AIProvider

_logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD) — updated 2026-06
# Source: https://platform.claude.com/docs/en/about-claude/pricing
ANTHROPIC_MODELS = {
    'claude-haiku-4-5-20251001': {
        'name': 'Claude Haiku 4.5 (fast, affordable)',
        'input_price': 1.00,
        'output_price': 5.00,
    },
    'claude-sonnet-4-5-20250929': {
        'name': 'Claude Sonnet 4.5 (balanced)',
        'input_price': 3.00,
        'output_price': 15.00,
    },
    'claude-sonnet-4-6': {
        'name': 'Claude Sonnet 4.6 (balanced, latest)',
        'input_price': 3.00,
        'output_price': 15.00,
    },
    'claude-opus-4-6': {
        'name': 'Claude Opus 4.6 (high accuracy)',
        'input_price': 5.00,
        'output_price': 25.00,
    },
    'claude-opus-4-8': {
        'name': 'Claude Opus 4.8 (maximum accuracy, latest)',
        'input_price': 5.00,
        'output_price': 25.00,
    },
}
# NOTE: the Fable/Mythos model family is intentionally excluded. Those models
# force adaptive thinking on, and forced tool use (``tool_choice`` of ``tool``
# or ``any``) is rejected with extended/adaptive thinking — this provider relies
# on forced tool use for guaranteed-valid JSON. They also cost ~2x Opus and
# require 30-day data retention. Opus 4.8 is the recommended maximum-accuracy
# option for invoice extraction.


class AnthropicProvider(AIProvider):
    """Anthropic (Claude) provider — full implementation.

    Uses raw HTTP via ``requests`` (no external dependency).
    Supports text and vision (base64 image) extraction modes.
    """

    API_ENDPOINT = 'https://api.anthropic.com/v1/messages'
    API_VERSION = '2023-06-01'
    MODELS = ANTHROPIC_MODELS

    # --- Public interface ---------------------------------------------------

    def get_provider_name(self):
        return 'Anthropic (Claude)'

    def supports_vision(self):
        return True

    def validate_api_key(self, api_key):
        """Validate by sending a minimal request to the API."""
        return self._validate_with_request(
            self.API_ENDPOINT,
            self._headers(api_key),
            {'model': 'claude-haiku-4-5-20251001', 'max_tokens': 10,
             'messages': [{'role': 'user', 'content': 'Hi'}]},
        )

    def extract(self, api_key, system_prompt, user_content, model):
        """Call the Claude API and return a structured result dict.

        Uses Anthropic's tool_use feature for guaranteed valid JSON output.
        """
        from .ai_prompt import EXTRACTION_TOOL_SCHEMA

        # Prompt caching: the tool schema + system prompt (chart of accounts,
        # taxes, vendor memory) are stable across every invoice of the same
        # vendor/company, while only the document content varies per request.
        # A cache breakpoint on the system block caches the tools+system prefix
        # (rendered before messages), giving ~90% input-token savings and lower
        # latency on batches.  Cache misses degrade gracefully to full price.
        payload = {
            'model': model,
            'max_tokens': self.MAX_OUTPUT_TOKENS,
            'system': [
                {
                    'type': 'text',
                    'text': system_prompt,
                    'cache_control': {'type': 'ephemeral'},
                },
            ],
            'messages': [{'role': 'user', 'content': user_content}],
            'tools': [
                {
                    'name': 'extract_invoice_data',
                    'description': (
                        'Extract structured invoice data from the document. '
                        'Return all fields you can identify. Use null for missing fields.'
                    ),
                    'input_schema': EXTRACTION_TOOL_SCHEMA,
                },
            ],
            'tool_choice': {'type': 'tool', 'name': 'extract_invoice_data'},
        }

        resp_or_error = self._call_with_retries(
            self.API_ENDPOINT,
            self._headers(api_key),
            payload,
        )
        if isinstance(resp_or_error, dict):
            return resp_or_error  # Error result
        data = self._safe_json(resp_or_error)
        if isinstance(data, dict) and 'error' in data and 'success' in data:
            return data  # JSON parse error
        return self._parse_success(data)

    # --- Internal helpers ---------------------------------------------------

    def _headers(self, api_key):
        return {
            'x-api-key': api_key,
            'anthropic-version': self.API_VERSION,
            'content-type': 'application/json',
        }

    @staticmethod
    def _parse_success(response_data):
        """Parse a successful API response.

        Handles both tool_use responses (structured output, preferred) and
        plain text responses (legacy fallback).
        """
        usage = response_data.get('usage', {})
        result = {
            'success': True,
            'input_tokens': usage.get('input_tokens', 0),
            'output_tokens': usage.get('output_tokens', 0),
            'model': response_data.get('model', ''),
        }

        content_blocks = response_data.get('content', [])

        # Primary: tool_use response (structured output — guaranteed valid JSON)
        for block in content_blocks:
            if block.get('type') == 'tool_use' and block.get('name') == 'extract_invoice_data':
                result['data'] = block.get('input', {})
                result['raw_text'] = json.dumps(result['data'], ensure_ascii=False)
                return result

        # Fallback: plain text response (legacy mode / no tool_use)
        text_parts = [b['text'] for b in content_blocks if b.get('type') == 'text']
        raw_text = '\n'.join(text_parts)
        result['raw_text'] = raw_text
        result['data'] = AIProvider._extract_json_from_text(raw_text)
        if result['data'] is None:
            result['parse_error'] = 'Could not parse JSON from AI response.'

        return result
