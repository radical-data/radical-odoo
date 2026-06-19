"""OpenAI (GPT) provider — full implementation.

Uses raw HTTP via ``requests`` (no external dependency).
Supports text and vision (base64 image) extraction modes.
Uses function calling with strict mode for guaranteed valid JSON output.

Designed for inheritance by OpenAI-compatible providers (e.g. xAI Grok).
Subclasses override ``API_ENDPOINT``, ``MODELS``, ``VALIDATION_MODEL``,
and ``get_provider_name()``.
"""

import json
import logging

from .ai_provider import AIProvider

_logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD) — updated 2025-06
# Source: https://openai.com/api/pricing/
OPENAI_MODELS = {
    'gpt-4o': {
        'name': 'GPT-4o (recommended)',
        'input_price': 2.50,
        'output_price': 10.00,
    },
    'gpt-4o-mini': {
        'name': 'GPT-4o Mini (fast, affordable)',
        'input_price': 0.15,
        'output_price': 0.60,
    },
}


class OpenAIProvider(AIProvider):
    """OpenAI (GPT) provider — full implementation.

    Uses raw HTTP via ``requests`` (no external dependency).
    Supports text and vision (base64 image) extraction modes.

    Designed for inheritance by OpenAI-compatible providers (e.g. xAI).
    Subclasses override class-level constants and ``get_provider_name()``.
    """

    API_ENDPOINT = 'https://api.openai.com/v1/chat/completions'
    MODELS = OPENAI_MODELS
    VALIDATION_MODEL = 'gpt-4o-mini'

    # --- Public interface ---------------------------------------------------

    def get_provider_name(self):
        return 'OpenAI (GPT)'

    def supports_vision(self):
        return True

    def validate_api_key(self, api_key):
        """Validate by sending a minimal request to the API."""
        return self._validate_with_request(
            self.API_ENDPOINT,
            self._headers(api_key),
            {'model': self.VALIDATION_MODEL, 'max_tokens': 5,
             'messages': [{'role': 'user', 'content': 'Hi'}]},
        )

    def extract(self, api_key, system_prompt, user_content, model):
        """Call the OpenAI Chat Completions API and return a structured result.

        Uses function calling with strict mode for guaranteed valid JSON.
        ``user_content`` is a list of content blocks in Anthropic format,
        converted internally to OpenAI format.
        """
        from .ai_prompt import EXTRACTION_TOOL_SCHEMA

        converted = self._convert_content(user_content)

        payload = {
            'model': model,
            'max_tokens': self.MAX_OUTPUT_TOKENS,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': converted},
            ],
            'tools': [
                {
                    'type': 'function',
                    'function': {
                        'name': 'extract_invoice_data',
                        'description': (
                            'Extract structured invoice data from the document. '
                            'Return all fields you can identify. Use null for missing fields.'
                        ),
                        'parameters': EXTRACTION_TOOL_SCHEMA,
                        # Keep non-strict for OpenAI. The upstream extraction
                        # schema works for tool calling, but OpenAI rejects it
                        # with HTTP 400 when strict structured-output
                        # validation is enabled.
                        'strict': False,
                    },
                },
            ],
            'tool_choice': {'type': 'function', 'function': {'name': 'extract_invoice_data'}},
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

    @staticmethod
    def _headers(api_key):
        return {
            'Authorization': 'Bearer %s' % api_key,
            'Content-Type': 'application/json',
        }

    @staticmethod
    def _convert_content(user_content):
        """Convert Anthropic-format content blocks to OpenAI format.

        Anthropic image: {'type': 'image', 'source': {'type': 'base64', ...}}
        OpenAI image:    {'type': 'image_url', 'image_url': {'url': 'data:...;base64,...'}}
        Text blocks are identical in both formats.
        """
        converted = []
        for block in user_content:
            if block.get('type') == 'image':
                source = block.get('source', {})
                media_type = source.get('media_type', 'image/png')
                data = source.get('data', '')
                converted.append(
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': 'data:%s;base64,%s' % (media_type, data),
                            'detail': 'high',
                        },
                    }
                )
            else:
                converted.append(block)
        return converted

    @staticmethod
    def _parse_success(response_data):
        """Parse a successful API response.

        Handles both function calling responses (preferred) and
        plain text responses (fallback).
        """
        usage = response_data.get('usage', {})
        result = {
            'success': True,
            'input_tokens': usage.get('prompt_tokens', 0),
            'output_tokens': usage.get('completion_tokens', 0),
            'model': response_data.get('model', ''),
        }

        choices = response_data.get('choices', [])
        if not choices:
            result['data'] = None
            result['raw_text'] = ''
            result['parse_error'] = 'No choices in API response.'
            return result

        message = choices[0].get('message', {})

        # Primary: function calling response (tool_calls)
        tool_calls = message.get('tool_calls', [])
        for tc in tool_calls:
            func = tc.get('function') or {}
            if func.get('name') == 'extract_invoice_data':
                args_str = func.get('arguments', '{}')
                try:
                    result['data'] = json.loads(args_str)
                except (json.JSONDecodeError, ValueError):
                    result['data'] = None
                    result['parse_error'] = 'Could not parse function arguments.'
                result['raw_text'] = args_str
                return result

        # Fallback: plain text response
        raw_text = message.get('content', '') or ''
        result['raw_text'] = raw_text
        result['data'] = AIProvider._extract_json_from_text(raw_text)
        if result['data'] is None and raw_text:
            result['parse_error'] = 'Could not parse JSON from AI response.'

        return result
