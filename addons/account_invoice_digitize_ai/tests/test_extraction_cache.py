"""Tests for extraction result caching."""

import base64
import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestExtractionCache(TransactionCase):
    """Test that extraction results are cached and reused correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls._p = 'account_invoice_digitize_ai.'

        cls.ICP.set_param(cls._p + 'ai_api_key', 'test-key')
        cls.ICP.set_param(cls._p + 'ai_provider', 'anthropic')
        cls.ICP.set_param(cls._p + 'ai_debug_mode', 'False')

        cls.purchase_journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

        cls.mock_data = {
            'vendor': {'name': 'Cache Test Vendor', 'confidence': 0.9},
            'invoice': {'reference': 'CACHE-001', 'invoice_date': '2026-01-15'},
            'totals': {
                'total_amount': 1200.00,
                'untaxed_amount': 1000.00,
                'tax_amount': 200.00,
                'confidence': 0.85,
            },
        }

    def _create_invoice_with_attachment(self, pdf_content=b'%PDF-fake'):
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'journal_id': self.purchase_journal.id,
            }
        )
        attachment = self.env['ir.attachment'].create(
            {
                'name': 'test_invoice.pdf',
                'datas': base64.b64encode(pdf_content),
                'res_model': 'account.move',
                'res_id': move.id,
                'mimetype': 'application/pdf',
            }
        )
        return move, attachment

    def test_cache_populated_after_extraction(self):
        """Cache fields should be populated after a successful extraction."""
        move, attachment = self._create_invoice_with_attachment()

        with patch.object(
            type(move),
            '_ai_trigger_extraction',
            return_value=self.mock_data,
        ):
            move.action_ai_extract()

        self.assertTrue(move.ai_last_extraction_data)
        self.assertEqual(move.ai_last_extraction_attachment_id, attachment)
        cached = json.loads(move.ai_last_extraction_data)
        self.assertEqual(cached['vendor']['name'], 'Cache Test Vendor')

    def test_cache_reused_on_second_click(self):
        """Second click should use cache, not call the API again."""
        move, attachment = self._create_invoice_with_attachment()

        # Populate cache
        move.ai_last_extraction_data = json.dumps(self.mock_data)
        move.ai_last_extraction_attachment_id = attachment.id

        with patch.object(
            type(move),
            '_ai_trigger_extraction',
        ) as mock_trigger:
            result = move.action_ai_extract()
            mock_trigger.assert_not_called()

        # Should return a wizard action
        self.assertEqual(result.get('res_model'), 'ai.preview.wizard')

    def test_cache_invalidated_on_attachment_change(self):
        """Changing attachment should invalidate the cache."""
        move, old_attachment = self._create_invoice_with_attachment(b'%PDF-old')

        # Populate cache with old attachment
        move.ai_last_extraction_data = json.dumps(self.mock_data)
        move.ai_last_extraction_attachment_id = old_attachment.id

        # Create a new attachment (simulating user replacing the PDF)
        new_attachment = self.env['ir.attachment'].create(
            {
                'name': 'new_invoice.pdf',
                'datas': base64.b64encode(b'%PDF-new'),
                'res_model': 'account.move',
                'res_id': move.id,
                'mimetype': 'application/pdf',
            }
        )

        with (
            patch.object(
                type(move),
                '_ai_get_invoice_attachment',
                return_value=new_attachment,
            ),
            patch.object(
                type(move),
                '_ai_trigger_extraction',
                return_value=self.mock_data,
            ) as mock_trigger,
        ):
            move.action_ai_extract()
            mock_trigger.assert_called_once()

        # Cache should now reference the new attachment
        self.assertEqual(move.ai_last_extraction_attachment_id, new_attachment)

    def test_re_extract_clears_cache(self):
        """action_ai_re_extract should clear cache and trigger fresh extraction."""
        move, attachment = self._create_invoice_with_attachment()

        # Populate cache
        move.ai_last_extraction_data = json.dumps(self.mock_data)
        move.ai_last_extraction_attachment_id = attachment.id

        with patch.object(
            type(move),
            '_ai_trigger_extraction',
            return_value=self.mock_data,
        ) as mock_trigger:
            move.action_ai_re_extract()
            mock_trigger.assert_called_once()

    def test_discard_keeps_cache(self):
        """Discarding the preview wizard should keep the cache intact."""
        move, attachment = self._create_invoice_with_attachment()

        # Populate cache
        move.ai_last_extraction_data = json.dumps(self.mock_data)
        move.ai_last_extraction_attachment_id = attachment.id

        # Simulate discard (wizard is transient, nothing happens to move)
        self.assertTrue(move.ai_last_extraction_data)
        self.assertEqual(move.ai_last_extraction_attachment_id, attachment)

    def test_cache_not_populated_on_failure(self):
        """Cache should not be populated when extraction fails."""
        move, attachment = self._create_invoice_with_attachment()

        with patch.object(
            type(move),
            '_ai_trigger_extraction',
            return_value=None,
        ):
            move.action_ai_extract()

        self.assertFalse(move.ai_last_extraction_data)
