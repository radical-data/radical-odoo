"""Factur-X pipeline integration tests.

Covers the Factur-X shortcut in the extraction pipeline:
  1. Factur-X detection skips AI API call
  2. Returns correct wrapper dict
  3. Non-PDF and unavailable cases
  4. Apply and preview modes
"""

import base64
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

_MODULE = 'odoo.addons.account_invoice_digitize_ai'

SAMPLE_FACTURX_XML = '<xml>facturx</xml>'


@tagged('post_install', '-at_install')
class TestFacturxPipeline(TransactionCase):
    """Test Factur-X shortcut in the extraction pipeline."""

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

    # --- 1. Shortcut skips AI call ---

    @patch(f'{_MODULE}.models.ai_document.detect_facturx', return_value=SAMPLE_FACTURX_XML)
    @patch(f'{_MODULE}.models.ai_document.FACTURX_AVAILABLE', True)
    @patch(f'{_MODULE}.models.ai_document.is_pdf', return_value=True)
    def test_facturx_shortcut_skips_ai_call(self, _is_pdf, _detect):
        """When Factur-X XML is found, no AI provider call should be made."""
        cfg = self.move._ai_get_config()
        mock_provider = MagicMock()
        with patch(f'{_MODULE}.models.ai_provider.get_provider', return_value=mock_provider):
            result = self.move._ai_run_pipeline(
                'test-key-123', cfg, b'fake-pdf', 'application/pdf',
            )
        # AI provider should NOT have been called
        mock_provider.extract.assert_not_called()
        # Result should be a Factur-X wrapper dict
        self.assertIsNotNone(result)
        self.assertTrue(result.get('_facturx'))

    # --- 2. Returns correct wrapper dict ---

    @patch(f'{_MODULE}.models.ai_document.detect_facturx', return_value=SAMPLE_FACTURX_XML)
    @patch(f'{_MODULE}.models.ai_document.FACTURX_AVAILABLE', True)
    @patch(f'{_MODULE}.models.ai_document.is_pdf', return_value=True)
    def test_facturx_returns_wrapper_dict(self, _is_pdf, _detect):
        """_ai_run_pipeline should return {'_facturx': True, '_xml': xml}."""
        cfg = self.move._ai_get_config()
        result = self.move._ai_run_pipeline(
            'test-key-123', cfg, b'fake-pdf', 'application/pdf',
        )
        self.assertEqual(result, {'_facturx': True, '_xml': SAMPLE_FACTURX_XML})

    # --- 3. Non-PDF skipped ---

    def test_facturx_non_pdf_skipped(self):
        """For image input, _ai_try_facturx should return None."""
        result = self.move._ai_try_facturx(b'fake-image', 'image/jpeg')
        self.assertIsNone(result)

    # --- 4. Unavailable skipped ---

    @patch(f'{_MODULE}.models.ai_document.FACTURX_AVAILABLE', False)
    def test_facturx_unavailable_skipped(self):
        """When facturx library is not installed, shortcut is skipped."""
        result = self.move._ai_try_facturx(b'fake-pdf', 'application/pdf')
        self.assertIsNone(result)

    # --- 5. Apply sets done ---

    @patch(f'{_MODULE}.models.ai_document.detect_facturx', return_value=SAMPLE_FACTURX_XML)
    @patch(f'{_MODULE}.models.ai_document.FACTURX_AVAILABLE', True)
    @patch(f'{_MODULE}.models.ai_document.is_pdf', return_value=True)
    @patch(f'{_MODULE}.models.ai_facturx_parser.parse_facturx_xml')
    def test_facturx_apply_sets_done(self, mock_parse, _is_pdf, _detect):
        """Successful Factur-X extraction should set status to 'done'."""
        mock_parse.return_value = {
            'vendor': {'name': 'Test Vendor', 'confidence': 1.0},
            'invoice': {'number': 'FX-001', 'date': '2024-01-15', 'confidence': 1.0},
            'totals': {'total_amount': 1200, 'confidence': 1.0},
            'document_type': 'invoice',
        }
        attachment = self.env['ir.attachment'].create({
            'name': 'test.pdf',
            'datas': base64.b64encode(b'fake-pdf'),
            'res_model': 'account.move',
            'res_id': self.move.id,
        })
        self.move._ai_trigger_extraction('test-key-123', attachment)
        self.assertEqual(self.move.ai_extraction_status, 'done')

    # --- 6. Preview returns data without applying ---

    @patch(f'{_MODULE}.models.ai_document.detect_facturx', return_value=SAMPLE_FACTURX_XML)
    @patch(f'{_MODULE}.models.ai_document.FACTURX_AVAILABLE', True)
    @patch(f'{_MODULE}.models.ai_document.is_pdf', return_value=True)
    def test_facturx_preview_returns_data(self, _is_pdf, _detect):
        """In preview mode, Factur-X data should be returned without applying."""
        attachment = self.env['ir.attachment'].create({
            'name': 'test.pdf',
            'datas': base64.b64encode(b'fake-pdf'),
            'res_model': 'account.move',
            'res_id': self.move.id,
        })
        result = self.move._ai_trigger_extraction(
            'test-key-123', attachment, preview=True,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.get('_facturx'))
        self.assertEqual(result['_xml'], SAMPLE_FACTURX_XML)
        # Status should NOT be 'done' (preview mode)
        self.assertNotEqual(self.move.ai_extraction_status, 'done')
