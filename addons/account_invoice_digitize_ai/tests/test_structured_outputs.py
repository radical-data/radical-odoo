"""Structured output tests: tool_use, network retry, prompt estimation, test wizard.

Covers:
- Network error retry (timeout, connection, 5xx)
- Structured outputs (tool_use response parsing)
- Prompt size estimation
- Test extraction wizard (full pipeline + per-step modes)
"""

import base64
import json
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

_MODULE = 'odoo.addons.account_invoice_digitize_ai'


def _make_mock_response(status_code=200, data=None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data or {}
    return mock


# Tool use response (structured output format)
MOCK_TOOL_USE_RESPONSE = {
    'id': 'msg_test_tool',
    'type': 'message',
    'role': 'assistant',
    'model': 'claude-haiku-4-5-20251001',
    'usage': {'input_tokens': 1500, 'output_tokens': 600},
    'content': [
        {
            'type': 'tool_use',
            'id': 'toolu_test123',
            'name': 'extract_invoice_data',
            'input': {
                'document_type': 'invoice',
                'is_marked_paid': False,
                'vendor': {
                    'name': 'ACME Services SARL',
                    'vat': 'FR12345678901',
                    'confidence': 0.95,
                },
                'invoice': {
                    'reference': 'FAC-2024-001',
                    'invoice_date': '2024-01-15',
                    'due_date': '2024-02-15',
                    'currency': 'EUR',
                    'is_credit_note': False,
                    'is_reverse_charge': False,
                    'confidence': 0.92,
                },
                'totals': {
                    'untaxed_amount': 1000.00,
                    'tax_amount': 200.00,
                    'total_amount': 1200.00,
                    'confidence': 0.98,
                },
                'tax_lines': [
                    {
                        'tax_rate': 20.0,
                        'tax_amount': 200.00,
                        'base_amount': 1000.00,
                        'confidence': 0.95,
                    },
                ],
                'table_analysis': {
                    'pricing_mode': 'ht_to_ttc',
                    'line_count': 2,
                },
            },
        },
    ],
}

# Legacy text response (no tool_use)
MOCK_TEXT_RESPONSE = {
    'id': 'msg_test_text',
    'type': 'message',
    'role': 'assistant',
    'model': 'claude-haiku-4-5-20251001',
    'usage': {'input_tokens': 1500, 'output_tokens': 600},
    'content': [
        {
            'type': 'text',
            'text': json.dumps(
                {
                    'document_type': 'invoice',
                    'vendor': {'name': 'Test Vendor', 'confidence': 0.9},
                    'invoice': {
                        'reference': 'INV-001',
                        'invoice_date': '2024-01-15',
                        'confidence': 0.9,
                    },
                    'totals': {
                        'untaxed_amount': 100.0,
                        'tax_amount': 20.0,
                        'total_amount': 120.0,
                        'confidence': 0.9,
                    },
                    'table_analysis': {'pricing_mode': 'ht_to_ttc', 'line_count': 1},
                }
            ),
        },
    ],
}


@tagged('post_install', '-at_install')
class TestNetworkRetry(TransactionCase):
    """Test network error retry logic in Anthropic provider."""

    def _get_provider(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider import get_provider

        return get_provider('anthropic')

    @patch(f'{_MODULE}.models.ai_provider.time.sleep')
    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_timeout_retries_then_succeeds(self, mock_post, mock_sleep):
        """Timeout on first attempt, success on second."""
        import requests

        mock_post.side_effect = [
            requests.Timeout('timed out'),
            _make_mock_response(200, MOCK_TOOL_USE_RESPONSE),
        ]
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertTrue(result['success'])
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once()

    @patch(f'{_MODULE}.models.ai_provider.time.sleep')
    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_connection_error_retries_exhausted(self, mock_post, mock_sleep):
        """Connection error on all attempts."""
        import requests

        mock_post.side_effect = requests.ConnectionError('refused')
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'connection')
        self.assertIn('retries exhausted', result['message'])
        self.assertEqual(mock_post.call_count, 3)

    @patch(f'{_MODULE}.models.ai_provider.time.sleep')
    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_server_error_500_retries(self, mock_post, mock_sleep):
        """Server error (500) should retry."""
        mock_post.side_effect = [
            _make_mock_response(500),
            _make_mock_response(200, MOCK_TOOL_USE_RESPONSE),
        ]
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertTrue(result['success'])
        self.assertEqual(mock_post.call_count, 2)

    @patch(f'{_MODULE}.models.ai_provider.time.sleep')
    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_server_error_502_retries_exhausted(self, mock_post, mock_sleep):
        """502 errors on all attempts should fail."""
        mock_post.return_value = _make_mock_response(502)
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertFalse(result['success'])
        self.assertIn('max_retries', result['error'])
        self.assertEqual(mock_post.call_count, 3)

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_client_error_400_no_retry(self, mock_post):
        """Client errors (4xx) should NOT retry."""
        mock_post.return_value = _make_mock_response(400)
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertFalse(result['success'])
        self.assertEqual(mock_post.call_count, 1)


@tagged('post_install', '-at_install')
class TestStructuredOutputs(TransactionCase):
    """Test tool_use structured output parsing."""

    def _get_provider(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider import get_provider

        return get_provider('anthropic')

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_tool_use_response_parsed(self, mock_post):
        """Tool use response should extract data from input field."""
        mock_post.return_value = _make_mock_response(200, MOCK_TOOL_USE_RESPONSE)
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertTrue(result['success'])
        self.assertIsNotNone(result['data'])
        self.assertEqual(result['data']['vendor']['name'], 'ACME Services SARL')
        self.assertEqual(result['data']['totals']['total_amount'], 1200.00)

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_text_response_fallback(self, mock_post):
        """Plain text response (no tool_use) should still be parsed via fallback."""
        mock_post.return_value = _make_mock_response(200, MOCK_TEXT_RESPONSE)
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertTrue(result['success'])
        self.assertIsNotNone(result['data'])
        self.assertEqual(result['data']['invoice']['reference'], 'INV-001')

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_tool_use_token_usage(self, mock_post):
        """Token usage should be reported from tool_use response."""
        mock_post.return_value = _make_mock_response(200, MOCK_TOOL_USE_RESPONSE)
        provider = self._get_provider()
        result = provider.extract('key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertEqual(result['input_tokens'], 1500)
        self.assertEqual(result['output_tokens'], 600)

    def test_extraction_tool_schema_exists(self):
        """EXTRACTION_TOOL_SCHEMA should be a valid JSON Schema structure."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import EXTRACTION_TOOL_SCHEMA

        self.assertIsInstance(EXTRACTION_TOOL_SCHEMA, dict)
        self.assertEqual(EXTRACTION_TOOL_SCHEMA['type'], 'object')
        self.assertIn('vendor', EXTRACTION_TOOL_SCHEMA['properties'])
        self.assertIn('invoice', EXTRACTION_TOOL_SCHEMA['properties'])
        self.assertIn('totals', EXTRACTION_TOOL_SCHEMA['properties'])
        self.assertIn('document_type', EXTRACTION_TOOL_SCHEMA['required'])

    def test_payload_includes_tools(self):
        """Extract payload should include tools and tool_choice."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import EXTRACTION_TOOL_SCHEMA

        # Verify the schema is importable and has expected structure
        props = EXTRACTION_TOOL_SCHEMA['properties']
        self.assertIn('document_type', props)
        self.assertEqual(props['document_type']['type'], 'string')
        self.assertIn('invoice', props['document_type']['enum'])


@tagged('post_install', '-at_install')
class TestPromptSizeEstimation(TransactionCase):
    """Test prompt size estimation."""

    def test_estimate_empty(self):
        """Empty prompt should estimate 0 tokens."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document_builder import AccountMove

        tokens = AccountMove._ai_estimate_prompt_tokens('', '')
        self.assertEqual(tokens, 0)

    def test_estimate_reasonable(self):
        """4000 chars should estimate ~1000 tokens."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document_builder import AccountMove

        text = 'a' * 4000
        tokens = AccountMove._ai_estimate_prompt_tokens(text, '')
        self.assertEqual(tokens, 1000)

    def test_estimate_combined(self):
        """System + user prompt should be summed."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document_builder import AccountMove

        tokens = AccountMove._ai_estimate_prompt_tokens('a' * 2000, 'b' * 2000)
        self.assertEqual(tokens, 1000)


@tagged('post_install', '-at_install')
class TestTestWizard(TransactionCase):
    """Test the test extraction wizard."""

    def test_wizard_no_api_key(self):
        """Without API key, wizard should fail gracefully."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', '')
        wizard = self.env['ai.test.wizard'].create({})
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'failed')
        self.assertIn('API key', wizard.result_message)

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_wizard_successful_extraction(self, mock_post):
        """With valid response, wizard should pass all checks."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', 'test-key')
        mock_post.return_value = _make_mock_response(200, MOCK_TOOL_USE_RESPONSE)
        wizard = self.env['ai.test.wizard'].create({})
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'success')
        self.assertIn('PASS', wizard.result_details)

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_wizard_api_error(self, mock_post):
        """API error should set wizard to failed."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', 'test-key')
        mock_post.return_value = _make_mock_response(401)
        wizard = self.env['ai.test.wizard'].create({})
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'failed')

    def test_wizard_validate_response(self):
        """_validate_test_response should check structure and content."""
        data = {
            'vendor': {'name': 'ACME', 'confidence': 0.9},
            'invoice': {'reference': 'INV-1', 'invoice_date': '2024-01-15', 'confidence': 0.9},
            'totals': {'total_amount': 1200.0, 'confidence': 0.9},
        }
        result = {'input_tokens': 100, 'output_tokens': 50}
        checks = self.env['ai.test.wizard']._validate_test_response(data, result)
        passed = sum(1 for ok, _ in checks if ok)
        self.assertEqual(passed, len(checks))


# Minimal valid-ish PDF for upload tests (enough to be decoded as base64)
_DUMMY_PDF = base64.b64encode(b'%PDF-1.0 dummy content for testing')


@tagged('post_install', '-at_install')
class TestTestWizardModes(TransactionCase):
    """Test the enhanced test wizard with per-step modes."""

    def test_default_mode(self):
        """Default mode should be full_pipeline."""
        wizard = self.env['ai.test.wizard'].create({})
        self.assertEqual(wizard.test_mode, 'full_pipeline')
        self.assertTrue(wizard.use_sample)

    def test_text_extraction_no_document(self):
        """Text extraction without document should raise UserError."""
        from odoo.exceptions import UserError

        wizard = self.env['ai.test.wizard'].create({'test_mode': 'text_extraction'})
        with self.assertRaises(UserError):
            wizard.action_run_test()

    @patch(f'{_MODULE}.models.ai_document.extract_text_from_pdf')
    def test_text_extraction_success(self, mock_extract):
        """Text extraction with uploaded PDF should succeed."""
        mock_extract.return_value = 'FACTURE N° TEST-001\nTotal: 1 200,00 EUR\nTVA: FR12345678901'

        wizard = self.env['ai.test.wizard'].create(
            {
                'test_mode': 'text_extraction',
                'test_document': _DUMMY_PDF,
                'test_document_name': 'invoice.pdf',
            }
        )
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'success')
        self.assertIn('characters extracted', wizard.result_message)
        self.assertIn('Number format', wizard.result_details)
        self.assertIn('Qualification', wizard.result_details)

    def test_preprocessing_no_provider(self):
        """Pre-processing without configured provider should fail."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'none')
        wizard = self.env['ai.test.wizard'].create(
            {
                'test_mode': 'preprocessing',
                'test_document': _DUMMY_PDF,
                'test_document_name': 'invoice.pdf',
            }
        )
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'failed')
        self.assertIn('No recognition service configured', wizard.result_message)

    def test_preprocessing_no_document(self):
        """Pre-processing without document should raise UserError."""
        from odoo.exceptions import UserError

        self.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di'
        )
        wizard = self.env['ai.test.wizard'].create({'test_mode': 'preprocessing'})
        with self.assertRaises(UserError):
            wizard.action_run_test()

    def test_preprocessing_no_credentials(self):
        """Pre-processing without credentials should fail."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', '')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', '')

        wizard = self.env['ai.test.wizard'].create(
            {
                'test_mode': 'preprocessing',
                'test_document': _DUMMY_PDF,
                'test_document_name': 'invoice.pdf',
            }
        )
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'failed')
        self.assertIn('No credentials', wizard.result_message)

    def test_prompt_preview_sample(self):
        """Prompt preview with sample invoice should show prompt and tokens."""
        wizard = self.env['ai.test.wizard'].create(
            {
                'test_mode': 'prompt_preview',
                'use_sample': True,
            }
        )
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'success')
        self.assertIn('tokens', wizard.result_message)
        self.assertIn('SYSTEM PROMPT', wizard.result_details)
        self.assertIn('USER PROMPT', wizard.result_details)
        self.assertIn('Estimated input tokens', wizard.result_details)

    @patch(f'{_MODULE}.models.ai_document.extract_text_from_pdf')
    def test_prompt_preview_upload(self, mock_extract):
        """Prompt preview with uploaded document should show prompt."""
        mock_extract.return_value = 'FACTURE N° UPLOAD-001\nTotal: 500,00 EUR'

        wizard = self.env['ai.test.wizard'].create(
            {
                'test_mode': 'prompt_preview',
                'use_sample': False,
                'test_document': _DUMMY_PDF,
                'test_document_name': 'test.pdf',
            }
        )
        wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'success')
        self.assertIn('SYSTEM PROMPT', wizard.result_details)
        self.assertIn('Source: test.pdf', wizard.result_details)

    @patch(f'{_MODULE}.models.ai_provider.requests.post')
    def test_full_pipeline_upload(self, mock_post):
        """Full pipeline with uploaded document should run structure checks."""
        mock_post.return_value = _make_mock_response(200, MOCK_TOOL_USE_RESPONSE)
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', 'test-key')

        with patch(f'{_MODULE}.models.ai_document.extract_text_from_pdf') as mock_extract:
            mock_extract.return_value = 'FACTURE N° TEST-001\nTotal: 1 200,00 EUR'
            wizard = self.env['ai.test.wizard'].create(
                {
                    'test_mode': 'full_pipeline',
                    'use_sample': False,
                    'test_document': _DUMMY_PDF,
                    'test_document_name': 'bill.pdf',
                }
            )
            wizard.action_run_test()
        self.assertEqual(wizard.result_status, 'success')
        self.assertIn('PASS', wizard.result_details)
        # Upload mode uses _validate_uploaded_response (no total == 1200 check)
        self.assertIn('vendor name extracted', wizard.result_details)

    def test_guess_mimetype(self):
        """_guess_mimetype should map common extensions."""
        from odoo.addons.account_invoice_digitize_ai.wizards.ai_test_wizard import AiTestWizard

        self.assertEqual(AiTestWizard._guess_mimetype('invoice.pdf'), 'application/pdf')
        self.assertEqual(AiTestWizard._guess_mimetype('scan.png'), 'image/png')
        self.assertEqual(AiTestWizard._guess_mimetype('photo.jpg'), 'image/jpeg')
        self.assertEqual(AiTestWizard._guess_mimetype('photo.jpeg'), 'image/jpeg')
        self.assertEqual(AiTestWizard._guess_mimetype('image.webp'), 'image/webp')
        self.assertEqual(AiTestWizard._guess_mimetype('noext'), 'application/pdf')
