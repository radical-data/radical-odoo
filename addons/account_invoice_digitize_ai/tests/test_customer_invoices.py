"""Tests for customer invoice extraction feature."""

import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCustomerInvoices(TransactionCase):
    """Test extraction on customer invoices (out_invoice / out_refund)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls._p = 'account_invoice_digitize_ai.'

        # Create a test partner
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Customer Corp',
                'vat': 'FR98765432101',
            }
        )

        # Create a sale journal
        cls.sale_journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

        # Purchase journal for vendor bill comparison
        cls.purchase_journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

        # Sample extraction data
        cls.sample_data = {
            'vendor': {'name': 'Customer Corp', 'vat': 'FR98765432101', 'confidence': 0.9},
            'invoice': {
                'reference': 'CINV-2026-001',
                'invoice_date': '2026-01-20',
                'due_date': '2026-02-20',
                'confidence': 0.85,
            },
            'totals': {
                'total_amount': 600.00,
                'untaxed_amount': 500.00,
                'tax_amount': 100.00,
                'confidence': 0.8,
            },
            'lines': [
                {
                    'description': 'Service delivery',
                    'quantity': 1,
                    'unit_price': 500.00,
                    'tax_rate': 20.0,
                },
            ],
            'document_type': 'invoice',
        }

    def _create_out_invoice(self):
        return self.env['account.move'].create(
            {
                'move_type': 'out_invoice',
                'journal_id': self.sale_journal.id,
            }
        )

    def _create_in_invoice(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'journal_id': self.purchase_journal.id,
            }
        )

    def _enable_customer_extraction(self):
        self.ICP.set_param(self._p + 'ai_extract_customer_invoices', 'True')

    def _disable_customer_extraction(self):
        self.ICP.set_param(self._p + 'ai_extract_customer_invoices', 'False')

    # ---------------------------------------------------------------
    # Button visibility
    # ---------------------------------------------------------------

    def test_button_hidden_by_default(self):
        """Button should be hidden on out_invoice by default."""
        self._disable_customer_extraction()
        move = self._create_out_invoice()
        self.assertFalse(move.ai_show_extract_button)

    def test_button_visible_when_enabled(self):
        """Button should be visible on out_invoice when setting is enabled."""
        self._enable_customer_extraction()
        move = self._create_out_invoice()
        self.assertTrue(move.ai_show_extract_button)

    def test_button_always_visible_on_vendor_bill(self):
        """Button should always be visible on in_invoice regardless of setting."""
        self._disable_customer_extraction()
        move = self._create_in_invoice()
        self.assertTrue(move.ai_show_extract_button)

    def test_button_hidden_on_other_move_types(self):
        """Button should be hidden on entry/payment move types."""
        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', self.company.id)],
            limit=1,
        )
        if journal:
            move = self.env['account.move'].create(
                {
                    'move_type': 'entry',
                    'journal_id': journal.id,
                }
            )
            self.assertFalse(move.ai_show_extract_button)

    # ---------------------------------------------------------------
    # Free mode enforcement
    # ---------------------------------------------------------------

    def test_forces_free_mode(self):
        """Extraction on out_invoice should force free mode."""
        self._enable_customer_extraction()
        move = self._create_out_invoice()

        # Set guided mode in config
        self.ICP.set_param(self._p + 'ai_extraction_mode', 'guided')
        self.ICP.set_param(self._p + 'ai_provider', 'anthropic')
        self.ICP.set_param(self._p + 'ai_api_key', 'test-key')

        # Apply extraction — should use free mode despite guided config
        with patch.object(type(move), '_ai_map_header_fields') as mock_map:
            mock_map.return_value = ({}, {}, None, None)
            move._ai_apply_extraction(self.sample_data)
            # Verify that _ai_map_header_fields was called with mode='free'
            call_args = mock_map.call_args
            self.assertEqual(call_args.kwargs.get('mode', call_args[1].get('mode')), 'free')

    def test_vendor_bill_keeps_configured_mode(self):
        """Extraction on in_invoice should use the configured mode."""
        move = self._create_in_invoice()
        self.ICP.set_param(self._p + 'ai_extraction_mode', 'guided')

        with patch.object(type(move), '_ai_map_header_fields') as mock_map:
            mock_map.return_value = ({}, {}, None, None)
            move._ai_apply_extraction(self.sample_data)
            call_args = mock_map.call_args
            self.assertEqual(call_args.kwargs.get('mode', call_args[1].get('mode')), 'guided')

    # ---------------------------------------------------------------
    # Helper method
    # ---------------------------------------------------------------

    def test_is_customer_invoice(self):
        """_ai_is_customer_invoice should return True for out types."""
        out_inv = self._create_out_invoice()
        self.assertTrue(out_inv._ai_is_customer_invoice())

        in_inv = self._create_in_invoice()
        self.assertFalse(in_inv._ai_is_customer_invoice())

    # ---------------------------------------------------------------
    # Credit note conversion
    # ---------------------------------------------------------------

    def test_credit_note_conversion_out_invoice(self):
        """Credit note detection should convert out_invoice to out_refund."""
        self._enable_customer_extraction()
        move = self._create_out_invoice()

        data = dict(self.sample_data)
        data['document_type'] = 'credit_note'

        move._ai_apply_extraction(data)
        self.assertEqual(move.move_type, 'out_refund')

    def test_credit_note_conversion_in_invoice(self):
        """Credit note detection should convert in_invoice to in_refund."""
        move = self._create_in_invoice()

        data = dict(self.sample_data)
        data['document_type'] = 'credit_note'

        move._ai_apply_extraction(data)
        self.assertEqual(move.move_type, 'in_refund')

    # ---------------------------------------------------------------
    # Lines extraction
    # ---------------------------------------------------------------

    def test_lines_applied_on_out_invoice(self):
        """Lines should be applied on out_invoice (extended filter)."""
        self._enable_customer_extraction()
        move = self._create_out_invoice()

        with patch.object(type(move), '_ai_apply_lines') as mock_lines:
            move._ai_apply_extraction(self.sample_data)
            mock_lines.assert_called_once()

    def test_lines_applied_on_in_invoice(self):
        """Lines should be applied on in_invoice (baseline check)."""
        move = self._create_in_invoice()

        with patch.object(type(move), '_ai_apply_lines') as mock_lines:
            move._ai_apply_extraction(self.sample_data)
            mock_lines.assert_called_once()

    # ---------------------------------------------------------------
    # Skip vendor memory / vendor score
    # ---------------------------------------------------------------

    def test_skips_vendor_score(self):
        """Vendor score should NOT be updated for customer invoices."""
        self._enable_customer_extraction()
        move = self._create_out_invoice()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score.AiVendorScore.update_score'
        ) as mock_score:
            move._ai_apply_extraction(self.sample_data)
            mock_score.assert_not_called()

    def test_skips_vendor_memory_learning(self):
        """Write corrections should NOT trigger vendor memory for customer invoices."""
        self._enable_customer_extraction()
        move = self._create_out_invoice()
        move.partner_id = self.partner.id

        # Simulate AI-extracted values
        move.ai_extracted_values = json.dumps({'ref': 'OLD-REF'})

        self.ICP.set_param(self._p + 'ai_extraction_mode', 'guided')

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory.AiVendorMemory.record_correction'
        ) as mock_correct:
            move.write({'ref': 'NEW-REF'})
            mock_correct.assert_not_called()

    def test_vendor_memory_works_for_vendor_bills(self):
        """Write corrections should still trigger vendor memory for vendor bills."""
        move = self._create_in_invoice()
        move.partner_id = self.partner.id
        move.ai_extracted_values = json.dumps({'ref': 'OLD-REF'})

        self.ICP.set_param(self._p + 'ai_extraction_mode', 'guided')

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory.AiVendorMemory.record_correction'
        ) as mock_correct:
            move.write({'ref': 'NEW-REF'})
            mock_correct.assert_called_once()

    # ---------------------------------------------------------------
    # Configuration field
    # ---------------------------------------------------------------

    def test_config_field_default(self):
        """ai_extract_customer_invoices should default to False."""
        settings = self.env['res.config.settings'].create({})
        self.assertFalse(settings.ai_extract_customer_invoices)
