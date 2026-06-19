"""Vision retry edge case tests.

Covers the decision logic in _ai_call_and_validate that triggers
(or skips) vision retry based on cross-validation failures, document
type, and current mode.
"""

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

_MODULE = 'odoo.addons.account_invoice_digitize_ai'

_GOOD_DATA = {
    'vendor': {'name': 'Test', 'confidence': 0.9},
    'invoice': {'number': 'INV-001', 'confidence': 0.9},
    'totals': {'total_amount': 100, 'confidence': 0.9},
}

_API_RESULT = {
    'success': True,
    'data': _GOOD_DATA,
    'raw_text': '{}',
    'input_tokens': 100,
    'output_tokens': 50,
    'model': 'claude-haiku-4-5-20251001',
}

_CFG = {
    'provider_name': 'anthropic',
    'model_id': 'claude-haiku-4-5-20251001',
    'extract_lines': False,
    'debug_mode': False,
}


@tagged('post_install', '-at_install')
class TestVisionRetryEdgeCases(TransactionCase):
    """Test when vision retry is triggered or skipped."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': cls.company.id,
        })
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_api_key', 'test-key-123',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_provider', 'anthropic',
        )

    def _make_doc_info(self, is_vision=False, mimetype='application/pdf'):
        return {
            'text': 'some text',
            'is_vision': is_vision,
            'mimetype': mimetype,
            'raw_data': b'fake-pdf',
            'qr_data': [],
            'detected_number_format': None,
        }

    # --- No retry when few failures ---

    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=1)
    @patch.object(type(MagicMock()), '_ai_retry_vision', create=True)
    def test_no_retry_when_few_failures(self, _retry, mock_cv):
        """0-1 cross-validation failures should NOT trigger vision retry."""
        doc_info = self._make_doc_info()
        with (
            patch.object(type(self.move), '_ai_call_provider', return_value=_API_RESULT),
            patch.object(type(self.move), '_ai_retry_vision') as mock_retry,
            patch.object(type(self.move), '_ai_create_log'),
        ):
            result = self.move._ai_call_and_validate(
                'test-key', _CFG, 'system', [{'type': 'text', 'text': 'hi'}],
                'user prompt', doc_info,
            )
        self.assertIsNotNone(result)
        mock_retry.assert_not_called()

    # --- No retry when already in vision mode ---

    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=3)
    def test_no_retry_when_already_vision(self, _cv):
        """If already in vision mode, no retry even with many failures."""
        doc_info = self._make_doc_info(is_vision=True)
        with (
            patch.object(type(self.move), '_ai_call_provider', return_value=_API_RESULT),
            patch.object(type(self.move), '_ai_retry_vision') as mock_retry,
            patch.object(type(self.move), '_ai_create_log'),
        ):
            result = self.move._ai_call_and_validate(
                'test-key', _CFG, 'system', [{'type': 'text', 'text': 'hi'}],
                'user prompt', doc_info,
            )
        self.assertIsNotNone(result)
        mock_retry.assert_not_called()

    # --- No retry for image input ---

    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=3)
    def test_no_retry_for_image_input(self, _cv):
        """Image input (not PDF) should not trigger vision retry."""
        doc_info = self._make_doc_info(mimetype='image/jpeg')
        with (
            patch.object(type(self.move), '_ai_call_provider', return_value=_API_RESULT),
            patch.object(type(self.move), '_ai_retry_vision') as mock_retry,
            patch.object(type(self.move), '_ai_create_log'),
        ):
            result = self.move._ai_call_and_validate(
                'test-key', _CFG, 'system',
                [{'type': 'image', 'source': {'data': 'abc'}}],
                'user prompt', doc_info,
            )
        self.assertIsNotNone(result)
        mock_retry.assert_not_called()

    # --- Retry triggered on two failures ---

    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=2)
    def test_retry_triggered_on_two_failures(self, _cv):
        """>=2 failures on a PDF in text mode should trigger vision retry."""
        doc_info = self._make_doc_info()
        with (
            patch.object(type(self.move), '_ai_call_provider', return_value=_API_RESULT),
            patch.object(type(self.move), '_ai_retry_vision', return_value=None) as mock_retry,
            patch.object(type(self.move), '_ai_create_log'),
        ):
            self.move._ai_call_and_validate(
                'test-key', _CFG, 'system', [{'type': 'text', 'text': 'hi'}],
                'user prompt', doc_info,
            )
        mock_retry.assert_called_once()

    # --- Retry replaces data on success ---

    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=2)
    def test_retry_replaces_data_on_success(self, _cv):
        """When vision retry succeeds, its data should replace original."""
        better_data = {
            'vendor': {'name': 'Better', 'confidence': 0.99},
            'invoice': {'number': 'INV-002', 'confidence': 0.99},
            'totals': {'total_amount': 200, 'confidence': 0.99},
        }
        doc_info = self._make_doc_info()
        with (
            patch.object(type(self.move), '_ai_call_provider', return_value=_API_RESULT),
            patch.object(type(self.move), '_ai_retry_vision', return_value=better_data),
            patch.object(type(self.move), '_ai_create_log'),
        ):
            result = self.move._ai_call_and_validate(
                'test-key', _CFG, 'system', [{'type': 'text', 'text': 'hi'}],
                'user prompt', doc_info,
            )
        self.assertEqual(result['vendor']['name'], 'Better')

    # --- Retry keeps original on failure ---

    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=2)
    def test_retry_keeps_original_on_failure(self, _cv):
        """When vision retry returns None, original data should be kept."""
        doc_info = self._make_doc_info()
        with (
            patch.object(type(self.move), '_ai_call_provider', return_value=_API_RESULT),
            patch.object(type(self.move), '_ai_retry_vision', return_value=None),
            patch.object(type(self.move), '_ai_create_log'),
        ):
            result = self.move._ai_call_and_validate(
                'test-key', _CFG, 'system', [{'type': 'text', 'text': 'hi'}],
                'user prompt', doc_info,
            )
        self.assertEqual(result['vendor']['name'], 'Test')
