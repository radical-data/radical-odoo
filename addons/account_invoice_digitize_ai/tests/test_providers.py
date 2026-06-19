"""Tests for AI provider implementations (OpenAI, Google Gemini, xAI Grok)."""

import json
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


def _mock_response(status_code=200, json_data=None):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ── Sample response payloads ──────────────────────────────────────────────

_OPENAI_TOOL_RESPONSE = {
    'choices': [
        {
            'message': {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {
                        'id': 'call_abc',
                        'type': 'function',
                        'function': {
                            'name': 'extract_invoice_data',
                            'arguments': json.dumps(
                                {
                                    'document_type': 'invoice',
                                    'vendor': {'name': 'Test Corp', 'confidence': 0.95},
                                    'invoice': {
                                        'reference': 'INV-001',
                                        'invoice_date': '2025-01-15',
                                        'confidence': 0.9,
                                    },
                                    'totals': {
                                        'untaxed_amount': 1000.0,
                                        'tax_amount': 200.0,
                                        'total_amount': 1200.0,
                                        'confidence': 0.9,
                                    },
                                    'table_analysis': {
                                        'pricing_mode': 'ht_to_ttc',
                                        'line_count': 3,
                                    },
                                }
                            ),
                        },
                    },
                ],
            },
            'finish_reason': 'tool_calls',
        },
    ],
    'usage': {'prompt_tokens': 500, 'completion_tokens': 300, 'total_tokens': 800},
    'model': 'gpt-4o-2025-05-13',
}

_OPENAI_TEXT_RESPONSE = {
    'choices': [
        {
            'message': {
                'role': 'assistant',
                'content': '{"document_type": "invoice", "vendor": {"name": "Test", "confidence": 0.8}}',
            },
            'finish_reason': 'stop',
        },
    ],
    'usage': {'prompt_tokens': 400, 'completion_tokens': 100},
    'model': 'gpt-4o-mini',
}

_GEMINI_FUNCTION_RESPONSE = {
    'candidates': [
        {
            'content': {
                'role': 'model',
                'parts': [
                    {
                        'functionCall': {
                            'name': 'extract_invoice_data',
                            'args': {
                                'document_type': 'invoice',
                                'vendor': {'name': 'Test Corp', 'confidence': 0.95},
                                'invoice': {
                                    'reference': 'FA-2025-001',
                                    'invoice_date': '2025-01-15',
                                    'confidence': 0.9,
                                },
                                'totals': {
                                    'untaxed_amount': 1000.0,
                                    'tax_amount': 200.0,
                                    'total_amount': 1200.0,
                                    'confidence': 0.9,
                                },
                                'table_analysis': {
                                    'pricing_mode': 'ht_to_ttc',
                                    'line_count': 2,
                                },
                            },
                        },
                    },
                ],
            },
            'finishReason': 'STOP',
        },
    ],
    'usageMetadata': {
        'promptTokenCount': 450,
        'candidatesTokenCount': 280,
        'totalTokenCount': 730,
    },
    'modelVersion': 'gemini-2.0-flash',
}

_GEMINI_TEXT_RESPONSE = {
    'candidates': [
        {
            'content': {
                'role': 'model',
                'parts': [
                    {'text': '{"document_type": "invoice", "vendor": {"name": "Test", "confidence": 0.8}}'},
                ],
            },
            'finishReason': 'STOP',
        },
    ],
    'usageMetadata': {'promptTokenCount': 400, 'candidatesTokenCount': 100},
}


# ── Sample content blocks (Anthropic format) ─────────────────────────────

_TEXT_CONTENT = [{'type': 'text', 'text': 'Extract data from this invoice.'}]

_IMAGE_CONTENT = [
    {
        'type': 'image',
        'source': {
            'type': 'base64',
            'media_type': 'image/png',
            'data': 'iVBORw0KGgo=',
        },
    },
    {'type': 'text', 'text': 'Extract data from this invoice image.'},
]


# ═════════════════════════════════════════════════════════════════════════
# OpenAI Provider Tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestOpenAIProvider(TransactionCase):
    """Test OpenAI (GPT) provider implementation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from ..models.ai_provider_openai import OpenAIProvider

        cls.provider = OpenAIProvider()

    def test_provider_name(self):
        self.assertEqual(self.provider.get_provider_name(), 'OpenAI (GPT)')

    def test_supports_vision(self):
        self.assertTrue(self.provider.supports_vision())

    def test_available_models(self):
        models = self.provider.get_available_models()
        self.assertGreaterEqual(len(models), 2)
        ids = [m['id'] for m in models]
        self.assertIn('gpt-4o', ids)
        self.assertIn('gpt-4o-mini', ids)
        for m in models:
            self.assertIn('name', m)
            self.assertIn('input_price', m)
            self.assertIn('output_price', m)

    def test_estimate_cost(self):
        cost = self.provider.estimate_cost(1_000_000, 1_000_000, 'gpt-4o')
        self.assertAlmostEqual(cost, 12.50)  # 2.50 + 10.00

    def test_estimate_cost_mini(self):
        cost = self.provider.estimate_cost(1_000_000, 1_000_000, 'gpt-4o-mini')
        self.assertAlmostEqual(cost, 0.75)  # 0.15 + 0.60

    @patch('requests.post')
    def test_validate_key_success(self, mock_post):
        mock_post.return_value = _mock_response(200)
        success, msg = self.provider.validate_api_key('sk-test-key')
        self.assertTrue(success)
        self.assertIn('successful', msg.lower())

    @patch('requests.post')
    def test_validate_key_invalid(self, mock_post):
        mock_post.return_value = _mock_response(401)
        success, msg = self.provider.validate_api_key('sk-bad-key')
        self.assertFalse(success)
        self.assertIn('Invalid', msg)

    @patch('requests.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = _mock_response(200, _OPENAI_TOOL_RESPONSE)
        result = self.provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['document_type'], 'invoice')
        self.assertEqual(result['data']['vendor']['name'], 'Test Corp')
        self.assertEqual(result['input_tokens'], 500)
        self.assertEqual(result['output_tokens'], 300)

    @patch('requests.post')
    def test_extract_text_fallback(self, mock_post):
        mock_post.return_value = _mock_response(200, _OPENAI_TEXT_RESPONSE)
        result = self.provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o-mini')
        self.assertTrue(result['success'])
        self.assertIsNotNone(result['data'])
        self.assertEqual(result['data']['document_type'], 'invoice')

    @patch('requests.post')
    def test_extract_auth_error(self, mock_post):
        mock_post.return_value = _mock_response(401)
        result = self.provider.extract('bad-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'auth')

    @patch('requests.post')
    def test_extract_rate_limit_then_success(self, mock_post):
        mock_post.side_effect = [
            _mock_response(429),
            _mock_response(200, _OPENAI_TOOL_RESPONSE),
        ]
        with patch('time.sleep'):
            result = self.provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertTrue(result['success'])

    @patch('requests.post')
    def test_extract_timeout(self, mock_post):
        import requests as req

        mock_post.side_effect = req.Timeout('timed out')
        with patch('time.sleep'):
            result = self.provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'timeout')

    def test_content_conversion_text(self):
        from ..models.ai_provider_openai import OpenAIProvider

        converted = OpenAIProvider._convert_content(_TEXT_CONTENT)
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]['type'], 'text')

    def test_content_conversion_image(self):
        from ..models.ai_provider_openai import OpenAIProvider

        converted = OpenAIProvider._convert_content(_IMAGE_CONTENT)
        self.assertEqual(len(converted), 2)
        img = converted[0]
        self.assertEqual(img['type'], 'image_url')
        self.assertIn('data:image/png;base64,', img['image_url']['url'])
        self.assertEqual(img['image_url']['detail'], 'high')


# ═════════════════════════════════════════════════════════════════════════
# Google Gemini Provider Tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestGoogleProvider(TransactionCase):
    """Test Google (Gemini) provider implementation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from ..models.ai_provider_google import GoogleProvider

        cls.provider = GoogleProvider()

    def test_provider_name(self):
        self.assertEqual(self.provider.get_provider_name(), 'Google (Gemini)')

    def test_supports_vision(self):
        self.assertTrue(self.provider.supports_vision())

    def test_available_models(self):
        models = self.provider.get_available_models()
        self.assertGreaterEqual(len(models), 3)
        ids = [m['id'] for m in models]
        self.assertIn('gemini-2.0-flash', ids)
        self.assertIn('gemini-2.5-flash', ids)
        self.assertIn('gemini-2.5-pro', ids)

    def test_estimate_cost(self):
        cost = self.provider.estimate_cost(1_000_000, 1_000_000, 'gemini-2.0-flash')
        self.assertAlmostEqual(cost, 0.50)  # 0.10 + 0.40

    @patch('requests.post')
    def test_validate_key_success(self, mock_post):
        mock_post.return_value = _mock_response(200, {'candidates': []})
        success, msg = self.provider.validate_api_key('AIza-test-key')
        self.assertTrue(success)

    @patch('requests.post')
    def test_validate_key_invalid(self, mock_post):
        mock_post.return_value = _mock_response(400)
        success, msg = self.provider.validate_api_key('bad-key')
        self.assertFalse(success)

    @patch('requests.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = _mock_response(200, _GEMINI_FUNCTION_RESPONSE)
        result = self.provider.extract('AIza-key', 'system', _TEXT_CONTENT, 'gemini-2.0-flash')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['document_type'], 'invoice')
        self.assertEqual(result['data']['vendor']['name'], 'Test Corp')
        self.assertEqual(result['input_tokens'], 450)
        self.assertEqual(result['output_tokens'], 280)

    @patch('requests.post')
    def test_extract_text_fallback(self, mock_post):
        mock_post.return_value = _mock_response(200, _GEMINI_TEXT_RESPONSE)
        result = self.provider.extract('AIza-key', 'system', _TEXT_CONTENT, 'gemini-2.0-flash')
        self.assertTrue(result['success'])
        self.assertIsNotNone(result['data'])
        self.assertEqual(result['data']['document_type'], 'invoice')

    @patch('requests.post')
    def test_extract_auth_error(self, mock_post):
        mock_post.return_value = _mock_response(400)
        result = self.provider.extract('bad-key', 'system', _TEXT_CONTENT, 'gemini-2.0-flash')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'auth')

    @patch('requests.post')
    def test_extract_rate_limit_then_success(self, mock_post):
        mock_post.side_effect = [
            _mock_response(429),
            _mock_response(200, _GEMINI_FUNCTION_RESPONSE),
        ]
        with patch('time.sleep'):
            result = self.provider.extract('AIza-key', 'system', _TEXT_CONTENT, 'gemini-2.0-flash')
        self.assertTrue(result['success'])

    @patch('requests.post')
    def test_extract_timeout(self, mock_post):
        import requests as req

        mock_post.side_effect = req.Timeout('timed out')
        with patch('time.sleep'):
            result = self.provider.extract('AIza-key', 'system', _TEXT_CONTENT, 'gemini-2.0-flash')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'timeout')

    def test_content_conversion_text(self):
        from ..models.ai_provider_google import GoogleProvider

        parts = GoogleProvider._convert_content(_TEXT_CONTENT)
        self.assertEqual(len(parts), 1)
        self.assertIn('text', parts[0])

    def test_content_conversion_image(self):
        from ..models.ai_provider_google import GoogleProvider

        parts = GoogleProvider._convert_content(_IMAGE_CONTENT)
        self.assertEqual(len(parts), 2)
        img = parts[0]
        self.assertIn('inlineData', img)
        self.assertEqual(img['inlineData']['mimeType'], 'image/png')
        self.assertEqual(img['inlineData']['data'], 'iVBORw0KGgo=')

    @patch('requests.post')
    def test_extract_no_candidates(self, mock_post):
        mock_post.return_value = _mock_response(200, {'candidates': [], 'usageMetadata': {}})
        result = self.provider.extract('AIza-key', 'system', _TEXT_CONTENT, 'gemini-2.0-flash')
        self.assertTrue(result['success'])
        self.assertIsNone(result['data'])
        self.assertIn('parse_error', result)


# ═════════════════════════════════════════════════════════════════════════
# xAI (Grok) Provider Tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestXAIProvider(TransactionCase):
    """Test xAI (Grok) provider — inherits from OpenAI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from ..models.ai_provider_xai import XAIProvider

        cls.provider = XAIProvider()

    def test_inherits_openai(self):
        from ..models.ai_provider_openai import OpenAIProvider

        self.assertIsInstance(self.provider, OpenAIProvider)

    def test_provider_name(self):
        self.assertEqual(self.provider.get_provider_name(), 'xAI (Grok)')

    def test_endpoint(self):
        self.assertEqual(self.provider.API_ENDPOINT, 'https://api.x.ai/v1/chat/completions')

    def test_different_models(self):
        models = self.provider.get_available_models()
        ids = [m['id'] for m in models]
        self.assertIn('grok-3', ids)
        self.assertIn('grok-3-mini', ids)
        self.assertIn('grok-2', ids)
        # Should NOT have OpenAI models
        self.assertNotIn('gpt-4o', ids)

    def test_supports_vision(self):
        self.assertTrue(self.provider.supports_vision())

    @patch('requests.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = _mock_response(200, _OPENAI_TOOL_RESPONSE)
        result = self.provider.extract('xai-key', 'system', _TEXT_CONTENT, 'grok-3')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['document_type'], 'invoice')
        # Verify correct endpoint was called
        call_args = mock_post.call_args
        self.assertIn('api.x.ai', call_args[0][0])

    def test_estimate_cost(self):
        cost = self.provider.estimate_cost(1_000_000, 1_000_000, 'grok-3')
        self.assertAlmostEqual(cost, 18.00)  # 3.00 + 15.00


# ═════════════════════════════════════════════════════════════════════════
# DeepSeek Provider Tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestDeepSeekProvider(TransactionCase):
    """Test DeepSeek provider — inherits from OpenAI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from ..models.ai_provider_deepseek import DeepSeekProvider

        cls.provider = DeepSeekProvider()

    def test_inherits_openai(self):
        from ..models.ai_provider_openai import OpenAIProvider

        self.assertIsInstance(self.provider, OpenAIProvider)

    def test_provider_name(self):
        self.assertEqual(self.provider.get_provider_name(), 'DeepSeek')

    def test_endpoint(self):
        self.assertEqual(self.provider.API_ENDPOINT, 'https://api.deepseek.com/chat/completions')

    def test_different_models(self):
        models = self.provider.get_available_models()
        ids = [m['id'] for m in models]
        self.assertIn('deepseek-chat', ids)
        self.assertIn('deepseek-reasoner', ids)
        self.assertNotIn('gpt-4o', ids)

    def test_no_vision(self):
        self.assertFalse(self.provider.supports_vision())

    @patch('requests.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = _mock_response(200, _OPENAI_TOOL_RESPONSE)
        result = self.provider.extract('ds-key', 'system', _TEXT_CONTENT, 'deepseek-chat')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['document_type'], 'invoice')
        call_args = mock_post.call_args
        self.assertIn('api.deepseek.com', call_args[0][0])

    def test_estimate_cost(self):
        cost = self.provider.estimate_cost(1_000_000, 1_000_000, 'deepseek-chat')
        self.assertAlmostEqual(cost, 0.70)  # 0.28 + 0.42


# ═════════════════════════════════════════════════════════════════════════
# Mistral AI Provider Tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestMistralProvider(TransactionCase):
    """Test Mistral AI provider — inherits from OpenAI."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from ..models.ai_provider_mistral import MistralProvider

        cls.provider = MistralProvider()

    def test_inherits_openai(self):
        from ..models.ai_provider_openai import OpenAIProvider

        self.assertIsInstance(self.provider, OpenAIProvider)

    def test_provider_name(self):
        self.assertEqual(self.provider.get_provider_name(), 'Mistral AI')

    def test_endpoint(self):
        self.assertEqual(self.provider.API_ENDPOINT, 'https://api.mistral.ai/v1/chat/completions')

    def test_different_models(self):
        models = self.provider.get_available_models()
        ids = [m['id'] for m in models]
        self.assertIn('mistral-small-3-2-25-06', ids)
        self.assertIn('mistral-medium-3-1-25-08', ids)
        self.assertIn('mistral-large-3-25-12', ids)
        self.assertNotIn('gpt-4o', ids)

    def test_supports_vision(self):
        self.assertTrue(self.provider.supports_vision())

    @patch('requests.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = _mock_response(200, _OPENAI_TOOL_RESPONSE)
        result = self.provider.extract('ms-key', 'system', _TEXT_CONTENT, 'mistral-small-3-2-25-06')
        self.assertTrue(result['success'])
        self.assertEqual(result['data']['document_type'], 'invoice')
        call_args = mock_post.call_args
        self.assertIn('api.mistral.ai', call_args[0][0])

    def test_estimate_cost(self):
        cost = self.provider.estimate_cost(1_000_000, 1_000_000, 'mistral-large-3-25-12')
        self.assertAlmostEqual(cost, 2.00)  # 0.50 + 1.50


# ═════════════════════════════════════════════════════════════════════════
# Provider Factory Tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestProviderFactory(TransactionCase):
    """Test provider factory and model aggregation."""

    def test_get_provider_openai(self):
        from ..models.ai_provider import get_provider
        from ..models.ai_provider_openai import OpenAIProvider

        provider = get_provider('openai')
        self.assertIsInstance(provider, OpenAIProvider)

    def test_get_provider_google(self):
        from ..models.ai_provider import get_provider
        from ..models.ai_provider_google import GoogleProvider

        provider = get_provider('google')
        self.assertIsInstance(provider, GoogleProvider)

    def test_get_provider_xai(self):
        from ..models.ai_provider import get_provider
        from ..models.ai_provider_xai import XAIProvider

        provider = get_provider('xai')
        self.assertIsInstance(provider, XAIProvider)

    def test_get_provider_deepseek(self):
        from ..models.ai_provider import get_provider
        from ..models.ai_provider_deepseek import DeepSeekProvider

        provider = get_provider('deepseek')
        self.assertIsInstance(provider, DeepSeekProvider)

    def test_get_provider_mistral(self):
        from ..models.ai_provider import get_provider
        from ..models.ai_provider_mistral import MistralProvider

        provider = get_provider('mistral')
        self.assertIsInstance(provider, MistralProvider)

    def test_all_provider_models(self):
        from ..models.ai_provider import get_all_provider_models

        models = get_all_provider_models()
        model_ids = [m[0] for m in models]
        # Should include models from all active providers
        self.assertIn('gpt-4o', model_ids)
        self.assertIn('gemini-2.0-flash', model_ids)
        self.assertIn('grok-3', model_ids)
        self.assertIn('deepseek-chat', model_ids)
        self.assertIn('mistral-small-3-2-25-06', model_ids)
        # Anthropic models should also be present
        self.assertIn('claude-haiku-4-5-20251001', model_ids)


# ═════════════════════════════════════════════════════════════════════════
# Base class shared helpers
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestBaseProviderHelpers(TransactionCase):
    """Test AIProvider shared utility methods."""

    def test_make_error(self):
        from ..models.ai_provider import AIProvider

        err = AIProvider._make_error('auth', 'Invalid key')
        self.assertFalse(err['success'])
        self.assertEqual(err['error'], 'auth')
        self.assertEqual(err['message'], 'Invalid key')
        self.assertIsNone(err['data'])
        self.assertEqual(err['input_tokens'], 0)

    def test_extract_json_from_text_raw(self):
        from ..models.ai_provider import AIProvider

        data = AIProvider._extract_json_from_text('{"a": 1}')
        self.assertEqual(data, {'a': 1})

    def test_extract_json_from_text_code_block(self):
        from ..models.ai_provider import AIProvider

        text = 'Here is the result:\n```json\n{"key": "val"}\n```'
        data = AIProvider._extract_json_from_text(text)
        self.assertEqual(data, {'key': 'val'})

    def test_extract_json_from_text_braces(self):
        from ..models.ai_provider import AIProvider

        text = 'Some text before {"x": 42} and after'
        data = AIProvider._extract_json_from_text(text)
        self.assertEqual(data, {'x': 42})

    def test_extract_json_from_text_none(self):
        from ..models.ai_provider import AIProvider

        self.assertIsNone(AIProvider._extract_json_from_text(''))
        self.assertIsNone(AIProvider._extract_json_from_text(None))
        self.assertIsNone(AIProvider._extract_json_from_text('no json here'))

    @patch('requests.post')
    def test_server_error_retry_then_success(self, mock_post):
        """503 on first attempt, 200 on second."""
        from ..models.ai_provider_openai import OpenAIProvider

        provider = OpenAIProvider()
        mock_post.side_effect = [
            _mock_response(503),
            _mock_response(200, _OPENAI_TOOL_RESPONSE),
        ]
        with patch('time.sleep'):
            result = provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertTrue(result['success'])
        self.assertEqual(mock_post.call_count, 2)

    @patch('requests.post')
    def test_connection_error_retry(self, mock_post):
        """ConnectionError on all attempts."""
        import requests as req

        from ..models.ai_provider_openai import OpenAIProvider

        provider = OpenAIProvider()
        mock_post.side_effect = req.ConnectionError('refused')
        with patch('time.sleep'):
            result = provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'connection')
        self.assertEqual(mock_post.call_count, 3)  # MAX_RETRIES

    @patch('requests.post')
    def test_credits_error(self, mock_post):
        from ..models.ai_provider_openai import OpenAIProvider

        provider = OpenAIProvider()
        mock_post.return_value = _mock_response(402)
        result = provider.extract('sk-key', 'system', _TEXT_CONTENT, 'gpt-4o')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'credits')


# ═════════════════════════════════════════════════════════════════════════
# Validator edge case tests
# ═════════════════════════════════════════════════════════════════════════


@tagged('post_install', '-at_install')
class TestValidatorEdgeCases(TransactionCase):
    """Test cross_validate edge cases and penalty separation."""

    def test_penalties_not_cascading(self):
        """Confidence penalties should be applied in one pass, not cascade."""
        from ..models import ai_validator

        data = {
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 999.0,  # Will fail total check
                'confidence': 1.0,
            },
            'invoice': {
                'invoice_date': '2025-06-01',
                'due_date': '2025-05-01',  # Will fail date check (due < invoice)
                'confidence': 1.0,
            },
            'vendor': {},
        }
        failures = ai_validator.cross_validate(data)
        self.assertEqual(failures, 2)
        # Both penalties applied independently (not cascading)
        self.assertEqual(data['totals']['confidence'], 0.5)
        self.assertEqual(data['invoice']['confidence'], 0.5)

    def test_valid_data_no_penalties(self):
        from ..models import ai_validator

        data = {
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.9,
            },
            'invoice': {
                'invoice_date': '2025-01-01',
                'due_date': '2025-02-01',
                'confidence': 0.9,
            },
            'vendor': {},
        }
        failures = ai_validator.cross_validate(data)
        self.assertEqual(failures, 0)
        self.assertEqual(data['totals']['confidence'], 0.9)
        self.assertEqual(data['invoice']['confidence'], 0.9)

    def test_iban_valid(self):
        from ..models import ai_validator

        data = {'vendor': {'iban': 'GB29 NWBK 6016 1331 9268 19', 'confidence': 0.9}}
        penalties = []
        result = ai_validator._validate_iban(data, penalties)
        self.assertEqual(result, 0)
        self.assertTrue(data['vendor']['iban_valid'])
        self.assertEqual(len(penalties), 0)

    def test_iban_invalid_checksum(self):
        from ..models import ai_validator

        data = {'vendor': {'iban': 'GB00 NWBK 6016 1331 9268 19', 'confidence': 0.9}}
        penalties = []
        result = ai_validator._validate_iban(data, penalties)
        self.assertEqual(result, 1)
        self.assertFalse(data['vendor']['iban_valid'])
        self.assertEqual(len(penalties), 1)

    def test_zero_amounts(self):
        """Zero values should not trigger false failures."""
        from ..models import ai_validator

        data = {
            'totals': {
                'untaxed_amount': 0.0,
                'tax_amount': 0.0,
                'total_amount': 0.0,
                'confidence': 0.9,
            },
            'vendor': {},
            'invoice': {},
        }
        failures = ai_validator.cross_validate(data)
        self.assertEqual(failures, 0)
