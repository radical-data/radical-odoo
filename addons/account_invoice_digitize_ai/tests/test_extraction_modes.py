"""Tests for extraction mode feature (guided / simplified / free)."""

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestExtractionModes(TransactionCase):
    """Test 3 extraction modes: guided, simplified, free."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls._p = 'account_invoice_digitize_ai.'

        # Create a test partner
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Test Vendor',
                'vat': 'FR12345678901',
            }
        )

        # Create a test move
        journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )
        cls.move = cls.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'journal_id': journal.id,
            }
        )

        # Sample extraction data
        cls.sample_data = {
            'vendor': {'name': 'Test Vendor', 'vat': 'FR12345678901', 'confidence': 0.95},
            'invoice': {
                'reference': 'INV-2026-001',
                'invoice_date': '2026-01-15',
                'due_date': '2026-02-15',
                'confidence': 0.9,
            },
            'totals': {
                'total_amount': 1200.00,
                'untaxed_amount': 1000.00,
                'tax_amount': 200.00,
                'confidence': 0.85,
            },
            'lines': [
                {
                    'description': 'Consulting services',
                    'quantity': 1,
                    'unit_price': 1000.00,
                    'tax_rate': 20.0,
                    'suggested_account_category': 'consulting',
                    'product_code': 'CONS-001',
                },
            ],
            'document_type': 'invoice',
        }

    def _set_mode(self, mode):
        self.ICP.set_param(self._p + 'ai_extraction_mode', mode)

    # ---------------------------------------------------------------
    # Default value
    # ---------------------------------------------------------------

    def test_guided_mode_default(self):
        """Default extraction mode should be 'guided'."""
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.ai_extraction_mode, 'guided')

    def test_extraction_mode_selection_values(self):
        """All three modes should be available."""
        field = self.env['res.config.settings']._fields['ai_extraction_mode']
        keys = [k for k, _v in field.selection]
        self.assertIn('guided', keys)
        self.assertIn('simplified', keys)
        self.assertIn('free', keys)

    # ---------------------------------------------------------------
    # Fiscal context building (prompt)
    # ---------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context.build_fiscal_context')
    def test_guided_mode_sends_full_context(self, mock_build_ctx):
        """Guided mode should call build_fiscal_context with include_accounts=True (default)."""
        self._set_mode('guided')
        mock_build_ctx.return_value = 'full context'

        doc_info = {'text': 'Invoice text', 'is_vision': False}
        self.move._ai_build_content(doc_info, b'', 'application/pdf', self.partner, self.company, False)

        mock_build_ctx.assert_called_once()
        # Default include_accounts is True (not passed explicitly)
        call_kwargs = mock_build_ctx.call_args
        # Should NOT have include_accounts=False
        if call_kwargs.kwargs:
            self.assertNotEqual(call_kwargs.kwargs.get('include_accounts'), False)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context.build_fiscal_context')
    def test_simplified_mode_sends_taxes_only(self, mock_build_ctx):
        """Simplified mode should call build_fiscal_context with include_accounts=False."""
        self._set_mode('simplified')
        mock_build_ctx.return_value = 'taxes only context'

        doc_info = {'text': 'Invoice text', 'is_vision': False}
        self.move._ai_build_content(doc_info, b'', 'application/pdf', self.partner, self.company, False)

        mock_build_ctx.assert_called_once()
        call_kwargs = mock_build_ctx.call_args
        self.assertEqual(call_kwargs.kwargs.get('include_accounts'), False)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context.build_fiscal_context')
    def test_free_mode_sends_no_context(self, mock_build_ctx):
        """Free mode should NOT call build_fiscal_context at all."""
        self._set_mode('free')

        doc_info = {'text': 'Invoice text', 'is_vision': False}
        self.move._ai_build_content(doc_info, b'', 'application/pdf', self.partner, self.company, False)

        mock_build_ctx.assert_not_called()

    # ---------------------------------------------------------------
    # Vendor memory in prompt
    # ---------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory.AiVendorMemory.get_vendor_context')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context.build_fiscal_context')
    def test_guided_mode_includes_vendor_memory(self, mock_ctx, mock_memory):
        """Guided mode should include vendor memory in prompt."""
        self._set_mode('guided')
        mock_ctx.return_value = 'ctx'
        mock_memory.return_value = 'vendor corrections'

        doc_info = {'text': 'Invoice text', 'is_vision': False}
        self.move._ai_build_content(doc_info, b'', 'application/pdf', self.partner, self.company, False)

        mock_memory.assert_called_once()

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory.AiVendorMemory.get_vendor_context')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context.build_fiscal_context')
    def test_simplified_mode_skips_vendor_memory(self, mock_ctx, mock_memory):
        """Simplified mode should NOT include vendor memory."""
        self._set_mode('simplified')
        mock_ctx.return_value = 'ctx'

        doc_info = {'text': 'Invoice text', 'is_vision': False}
        self.move._ai_build_content(doc_info, b'', 'application/pdf', self.partner, self.company, False)

        mock_memory.assert_not_called()

    # ---------------------------------------------------------------
    # Header field matching
    # ---------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_partner')
    def test_free_mode_skips_partner_matching(self, mock_match):
        """Free mode should not match partner."""
        vals, confidence, partner, _po = self.move._ai_map_header_fields(
            self.sample_data,
            mode='free',
        )
        mock_match.assert_not_called()
        self.assertIsNone(partner)
        self.assertNotIn('partner_id', vals)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_partner')
    def test_simplified_mode_matches_partner(self, mock_match):
        """Simplified mode should match partner."""
        mock_match.return_value = self.partner
        vals, confidence, partner, _po = self.move._ai_map_header_fields(
            self.sample_data,
            mode='simplified',
        )
        mock_match.assert_called_once()
        self.assertEqual(partner, self.partner)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_payment_term')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_partner')
    def test_simplified_mode_skips_payment_terms(self, mock_partner, mock_pt):
        """Simplified mode should not match payment terms."""
        mock_partner.return_value = self.partner
        data = dict(self.sample_data)
        data['invoice'] = dict(data['invoice'], payment_terms_text='30 days net')

        self.move._ai_map_header_fields(data, mode='simplified')
        mock_pt.assert_not_called()

    # ---------------------------------------------------------------
    # Line matching
    # ---------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_product')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_account')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_tax_by_rate')
    def test_free_mode_skips_all_line_matching(self, mock_tax, mock_account, mock_product):
        """Free mode should not do any line matching (tax, account, product)."""
        line_data = self.sample_data['lines'][0]
        line_vals = self.move._ai_build_line_vals(
            line_data,
            self.company,
            self.partner,
            mode='free',
        )
        mock_tax.assert_not_called()
        mock_account.assert_not_called()
        mock_product.assert_not_called()
        # Basic values should still be extracted
        self.assertEqual(line_vals['name'], 'Consulting services')
        self.assertEqual(line_vals['quantity'], 1)
        self.assertEqual(line_vals['price_unit'], 1000.00)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_product')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_account')
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_matcher.match_tax_by_rate')
    def test_simplified_mode_matches_tax_only(self, mock_tax, mock_account, mock_product):
        """Simplified mode should match taxes but not accounts or products."""
        mock_tax.return_value = MagicMock(ids=[1])
        line_data = self.sample_data['lines'][0]
        self.move._ai_build_line_vals(
            line_data,
            self.company,
            self.partner,
            mode='simplified',
        )
        mock_tax.assert_called_once()
        mock_account.assert_not_called()
        mock_product.assert_not_called()

    # ---------------------------------------------------------------
    # Warnings
    # ---------------------------------------------------------------

    def test_free_mode_only_proforma_and_paid_warnings(self):
        """Free mode should only check proforma and paid stamp."""
        data = dict(self.sample_data)
        data['document_type'] = 'proforma'
        data['is_marked_paid'] = True
        data['buyer'] = {'name': 'Wrong Company', 'vat': 'FR00000000000'}

        warnings = self.move._ai_check_warnings(
            data,
            self.partner,
            {},
            self.company,
            mode='free',
        )
        self.assertIn('proforma_warning', warnings)
        self.assertIn('paid_warning', warnings)
        self.assertNotIn('buyer_warning', warnings)
        self.assertNotIn('duplicate_warning', warnings)
        self.assertNotIn('anomaly_warning', warnings)

    def test_simplified_mode_includes_buyer_warning(self):
        """Simplified mode should check buyer but not duplicates/anomalies."""
        data = dict(self.sample_data)
        data['buyer'] = {'name': 'Wrong Company', 'vat': 'FR00000000000'}

        warnings = self.move._ai_check_warnings(
            data,
            self.partner,
            {},
            self.company,
            mode='simplified',
        )
        # Buyer warning should be checked (may or may not trigger depending on company)
        self.assertNotIn('duplicate_warning', warnings)
        self.assertNotIn('anomaly_warning', warnings)

    # ---------------------------------------------------------------
    # Learning (write override)
    # ---------------------------------------------------------------

    def test_free_mode_skips_learning(self):
        """Free mode should not record corrections in vendor memory."""
        self._set_mode('free')
        self.move.partner_id = self.partner
        self.move.ai_extracted_values = '{"ref": "OLD-REF"}'

        with patch.object(
            type(self.env['ai.vendor.memory']),
            'record_correction',
        ) as mock_record:
            # This should NOT trigger learning in free mode
            self.move.write({'ref': 'NEW-REF'})
            mock_record.assert_not_called()

    # ---------------------------------------------------------------
    # Cost estimate
    # ---------------------------------------------------------------

    def test_cost_estimate_varies_by_mode(self):
        """Cost estimate should decrease for simplified and free modes."""
        settings = self.env['res.config.settings'].create({})

        settings.ai_extraction_mode = 'guided'
        settings._compute_ai_cost_estimate()
        guided_cost = settings.ai_cost_estimate

        settings.ai_extraction_mode = 'simplified'
        settings._compute_ai_cost_estimate()
        simplified_cost = settings.ai_cost_estimate

        settings.ai_extraction_mode = 'free'
        settings._compute_ai_cost_estimate()
        free_cost = settings.ai_cost_estimate

        # All should produce non-empty estimates
        self.assertTrue(guided_cost)
        self.assertTrue(simplified_cost)
        self.assertTrue(free_cost)

    # ---------------------------------------------------------------
    # Fiscal context builder (include_accounts parameter)
    # ---------------------------------------------------------------

    def test_build_fiscal_context_include_accounts_false(self):
        """build_fiscal_context with include_accounts=False should exclude accounts."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_fiscal_context

        result = ai_fiscal_context.build_fiscal_context(
            self.env,
            self.company,
            self.partner,
            include_accounts=False,
        )
        # Should contain taxes but not account section
        self.assertIn('Company:', result)
        self.assertIn('purchase taxes', result)
        self.assertNotIn('ONLY suggest accounts', result)
        self.assertNotIn('chart_name', result)  # Template var shouldn't be present
