"""Google (Gemini) provider — full implementation.

Uses raw HTTP via ``requests`` (no external dependency).
Supports text and vision (base64 image) extraction modes.
Uses function declarations for structured JSON output.
"""

import json
import logging

from .ai_provider import AIProvider

_logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD) — updated 2025-06
# Source: https://ai.google.dev/gemini-api/docs/pricing
GOOGLE_MODELS = {
    'gemini-2.0-flash': {
        'name': 'Gemini 2.0 Flash (fast, very affordable)',
        'input_price': 0.10,
        'output_price': 0.40,
    },
    'gemini-2.5-flash': {
        'name': 'Gemini 2.5 Flash (balanced)',
        'input_price': 0.30,
        'output_price': 2.50,
    },
    'gemini-2.5-pro': {
        'name': 'Gemini 2.5 Pro (best quality)',
        'input_price': 1.25,
        'output_price': 10.00,
    },
}

API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'


class GoogleProvider(AIProvider):
    """Google (Gemini) provider — full implementation.

    Uses raw HTTP via ``requests`` (no external dependency).
    Supports text and vision (inline base64 image) extraction modes.
    """

    MODELS = GOOGLE_MODELS
    VALIDATION_MODEL = 'gemini-2.0-flash'

    # --- Public interface ---------------------------------------------------

    def get_provider_name(self):
        return 'Google (Gemini)'

    def supports_vision(self):
        return True

    def validate_api_key(self, api_key):
        """Validate by sending a minimal request to the Gemini API."""
        return self._validate_with_request(
            '%s/%s:generateContent' % (API_BASE, self.VALIDATION_MODEL),
            self._headers(api_key),
            {'contents': [{'role': 'user', 'parts': [{'text': 'Hi'}]}], 'generationConfig': {'maxOutputTokens': 5}},
        )

    def extract(self, api_key, system_prompt, user_content, model):
        """Call the Gemini generateContent API and return a structured result.

        Uses function declarations for structured JSON output.
        ``user_content`` is a list of content blocks in Anthropic format,
        converted internally to Gemini format.
        """
        from .ai_prompt import EXTRACTION_TOOL_SCHEMA

        parts = self._convert_content(user_content)
        endpoint = '%s/%s:generateContent' % (API_BASE, model)

        payload = {
            'systemInstruction': {'parts': [{'text': system_prompt}]},
            'contents': [{'role': 'user', 'parts': parts}],
            'generationConfig': {'maxOutputTokens': self.MAX_OUTPUT_TOKENS, 'temperature': 0.1},
            'tools': [
                {
                    'functionDeclarations': [
                        {
                            'name': 'extract_invoice_data',
                            'description': (
                                'Extract structured invoice data from the document. '
                                'Return all fields you can identify. Use null for missing fields.'
                            ),
                            'parameters': EXTRACTION_TOOL_SCHEMA,
                        },
                    ],
                },
            ],
            'toolConfig': {'functionCallingConfig': {'mode': 'ANY'}},
        }

        resp_or_error = self._call_with_retries(
            endpoint,
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
            'x-goog-api-key': api_key,
            'Content-Type': 'application/json',
        }

    @staticmethod
    def _convert_content(user_content):
        """Convert Anthropic-format content blocks to Gemini format.

        Anthropic image: {'type': 'image', 'source': {'type': 'base64', 'media_type': ..., 'data': ...}}
        Gemini image:    {'inlineData': {'mimeType': ..., 'data': ...}}
        Anthropic text:  {'type': 'text', 'text': '...'}
        Gemini text:     {'text': '...'}
        """
        parts = []
        for block in user_content:
            if block.get('type') == 'image':
                source = block.get('source', {})
                parts.append(
                    {
                        'inlineData': {
                            'mimeType': source.get('media_type', 'image/png'),
                            'data': source.get('data', ''),
                        },
                    }
                )
            elif block.get('type') == 'text':
                parts.append({'text': block.get('text', '')})
        return parts

    @staticmethod
    def _parse_success(response_data):
        """Parse a successful Gemini API response.

        Handles both function call responses (preferred) and
        plain text responses (fallback).
        """
        usage = response_data.get('usageMetadata', {})
        result = {
            'success': True,
            'input_tokens': usage.get('promptTokenCount', 0),
            'output_tokens': usage.get('candidatesTokenCount', 0),
            'model': response_data.get('modelVersion', ''),
        }

        candidates = response_data.get('candidates', [])
        if not candidates:
            result['data'] = None
            result['raw_text'] = ''
            result['parse_error'] = 'No candidates in API response.'
            return result

        content = candidates[0].get('content', {})
        parts = content.get('parts', [])

        # Primary: function call response
        for part in parts:
            fc = part.get('functionCall')
            if fc and fc.get('name') == 'extract_invoice_data':
                # Gemini returns args as a dict, not a JSON string
                result['data'] = fc.get('args', {})
                result['raw_text'] = json.dumps(result['data'], ensure_ascii=False)
                return result

        # Fallback: plain text response
        text_parts = [p.get('text', '') for p in parts if 'text' in p]
        raw_text = '\n'.join(text_parts)
        result['raw_text'] = raw_text
        result['data'] = AIProvider._extract_json_from_text(raw_text)
        if result['data'] is None and raw_text:
            result['parse_error'] = 'Could not parse JSON from AI response.'

        return result
