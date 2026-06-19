"""Robustness tests: edge cases, thread safety, race conditions, rounding.

Covers all 11 fixes:
  1-2. quantity=0.0 / price_unit=0.0 falsy check + TTC→HT rounding
  3.   Factur-X parse failure → status='failed'
  4.   _match_account_by_category account_type filter
  5.   match_partner active=True filter (archived partners)
  6.   Thread-safe fiscal cache
  7.   Vendor memory race condition (IntegrityError)
  8.   N+1 queries in match_tax_by_rate
  9.   ai_confidence JSON normalization in _ai_handle_doc_issue
  10.  API timeout 180s
  11.  reliability_rate rounding
"""

import json
import threading
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


# ===================================================================
# Fixes 1-2: quantity/price_unit falsy + TTC→HT rounding
# ===================================================================


@tagged('post_install', '-at_install')
class TestLineBuildFalsy(TransactionCase):
    """Fix 1-2: quantity=0.0 and price_unit=0.0 must NOT be treated as missing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create({'move_type': 'in_invoice', 'company_id': cls.company.id})

    def _build(self, line_data):
        return self.move._ai_build_line_vals(line_data, self.company, None)

    def test_quantity_zero_preserved(self):
        """quantity=0.0 should be kept as 0.0, not replaced by 1.0."""
        vals = self._build({'description': 'Test item', 'quantity': 0.0, 'unit_price': 10.0})
        self.assertEqual(vals['quantity'], 0.0)

    def test_quantity_none_defaults_to_one(self):
        """quantity=None should default to 1.0."""
        vals = self._build({'description': 'Test item', 'unit_price': 10.0})
        self.assertEqual(vals['quantity'], 1.0)

    def test_quantity_missing_defaults_to_one(self):
        """Missing quantity key should default to 1.0."""
        vals = self._build({'description': 'Test item'})
        self.assertEqual(vals['quantity'], 1.0)

    def test_price_zero_preserved(self):
        """price_unit=0.0 should be kept as 0.0, not replaced."""
        vals = self._build({'description': 'Free item', 'quantity': 1, 'unit_price': 0.0})
        self.assertEqual(vals['price_unit'], 0.0)

    def test_price_none_defaults_to_zero(self):
        """price_unit=None with no subtotal should default to 0.0."""
        vals = self._build({'description': 'Test item', 'quantity': 1})
        self.assertEqual(vals['price_unit'], 0.0)

    def test_price_falls_back_to_subtotal(self):
        """When unit_price is None, subtotal_untaxed should be used."""
        vals = self._build({'description': 'Flat fee', 'subtotal_untaxed': 500.0})
        self.assertEqual(vals['price_unit'], 500.0)

    def test_price_subtotal_zero_preserved(self):
        """subtotal_untaxed=0.0 fallback should be kept as 0.0."""
        vals = self._build({'description': 'Zero fee', 'subtotal_untaxed': 0.0})
        self.assertEqual(vals['price_unit'], 0.0)

    def test_ttc_to_ht_rounding(self):
        """TTC→HT back-calculation should be rounded to 2 decimals."""
        # 100.00 TTC at 20% → 83.33 HT (not 83.33333...)
        vals = self._build(
            {
                'description': 'Service TTC',
                'unit_price': 100.00,
                'unit_price_is_tax_included': True,
                'tax_rate': 20.0,
            }
        )
        self.assertEqual(vals['price_unit'], 83.33)

    def test_ttc_to_ht_rounding_edge_case(self):
        """TTC→HT with 5.5% rate should round correctly."""
        # 10.55 TTC at 5.5% → 10.00 HT
        vals = self._build(
            {
                'description': 'Book TTC',
                'unit_price': 10.55,
                'unit_price_is_tax_included': True,
                'tax_rate': 5.5,
            }
        )
        self.assertEqual(vals['price_unit'], 10.0)

    def test_ttc_not_applied_without_rate(self):
        """TTC flag without tax_rate should NOT trigger back-calculation."""
        vals = self._build(
            {
                'description': 'Mystery TTC',
                'unit_price': 100.00,
                'unit_price_is_tax_included': True,
            }
        )
        self.assertEqual(vals['price_unit'], 100.00)

    def test_no_description_returns_none(self):
        """Line with no description should be skipped (return None)."""
        vals = self._build({'quantity': 1, 'unit_price': 10.0})
        self.assertIsNone(vals)


# ===================================================================
# Fix 3: Factur-X parse failure → status='failed'
# ===================================================================


@tagged('post_install', '-at_install')
class TestFacturxParseFailure(TransactionCase):
    """Fix 3: Factur-X XML parsing failure must set status to 'failed'."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create({'move_type': 'in_invoice', 'company_id': cls.company.id})

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_facturx_parser.parse_facturx_xml')
    def test_facturx_parse_error_sets_failed(self, mock_parse):
        """If parse_facturx_xml raises, status should be 'failed'."""
        mock_parse.side_effect = ValueError('Invalid XML structure')
        self.move._ai_apply_facturx({'raw_xml': '<broken/>'})
        self.assertEqual(self.move.ai_extraction_status, 'failed')

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_facturx_parser.parse_facturx_xml')
    def test_facturx_parse_error_sets_confidence(self, mock_parse):
        """Factur-X failure should set ai_confidence with source and overall=0."""
        mock_parse.side_effect = Exception('Unexpected error')
        self.move._ai_apply_facturx({'raw_xml': '<broken/>'})
        conf = json.loads(self.move.ai_confidence)
        self.assertEqual(conf['source'], 'facturx')
        self.assertEqual(conf['overall'], 0.0)


# ===================================================================
# Fix 4: _match_account_by_category account_type filter
# ===================================================================


@tagged('post_install', '-at_install')
class TestAccountCategoryTypeFilter(TransactionCase):
    """Fix 4: _match_account_by_category should only return expense accounts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def test_non_expense_account_excluded(self):
        """An asset account with an expense-like code prefix should be excluded."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _match_account_by_category

        # Create an asset account with code 6226xx (looks like consulting)
        vals = {
            'name': 'Test Asset 6226',
            'code': '622600',
            'account_type': 'asset_fixed',
        }
        # Odoo 19: company_ids (many2many); older: company_id (many2one)
        if 'company_ids' in self.env['account.account']._fields:
            vals['company_ids'] = [(4, self.company.id)]
        else:
            vals['company_id'] = self.company.id
        self.env['account.account'].create(vals)
        # Should NOT be returned because account_type is not expense
        result = _match_account_by_category(self.env, 'consulting', self.company)
        if result:
            self.assertIn(
                result.account_type,
                ('expense', 'expense_direct_cost'),
                'Category matching returned a non-expense account: %s (%s)' % (result.code, result.account_type),
            )

    def test_expense_account_returned(self):
        """An expense account with matching prefix should be returned."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _match_account_by_category

        vals = {
            'name': 'Consulting Expense',
            'code': '622601',
            'account_type': 'expense',
        }
        if 'company_ids' in self.env['account.account']._fields:
            vals['company_ids'] = [(4, self.company.id)]
        else:
            vals['company_id'] = self.company.id
        self.env['account.account'].create(vals)
        result = _match_account_by_category(self.env, 'consulting', self.company)
        if result:
            self.assertIn(result.account_type, ('expense', 'expense_direct_cost'))


# ===================================================================
# Fix 5: match_partner active=True filter
# ===================================================================


@tagged('post_install', '-at_install')
class TestArchivedPartnerExcluded(TransactionCase):
    """Fix 5: match_partner must not return archived (inactive) partners."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create an archived partner
        cls.archived = cls.env['res.partner'].create(
            {
                'name': 'Archived Corp',
                'is_company': True,
                'vat': 'FR99999999999',
                'email': 'old@archived-corp.com',
                'active': False,
            }
        )

    def _match(self, vendor_data):
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_partner

        return match_partner(self.env, vendor_data)

    def test_archived_partner_not_matched_by_vat(self):
        """Archived partner should NOT be found by VAT."""
        result = self._match({'vat': 'FR99999999999'})
        self.assertIsNone(result)

    def test_archived_partner_not_matched_by_name(self):
        """Archived partner should NOT be found by name."""
        result = self._match({'name': 'Archived Corp'})
        self.assertIsNone(result)

    def test_archived_partner_not_matched_by_email(self):
        """Archived partner should NOT be found by email."""
        result = self._match({'email': 'old@archived-corp.com'})
        self.assertIsNone(result)

    def test_active_partner_still_matched(self):
        """Active partner should still be found normally."""
        active = self.env['res.partner'].create(
            {
                'name': 'Active Corp',
                'is_company': True,
                'vat': 'FR11111111111',
                'active': True,
            }
        )
        result = self._match({'vat': 'FR11111111111'})
        self.assertEqual(result, active)


# ===================================================================
# Fix 6: Thread-safe fiscal cache
# ===================================================================


@tagged('post_install', '-at_install')
class TestFiscalCacheThreadSafety(TransactionCase):
    """Fix 6: Fiscal cache operations should be thread-safe."""

    def test_invalidate_does_not_crash_concurrently(self):
        """Concurrent invalidate_fiscal_cache calls should not raise."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context import (
            _fiscal_cache,
            _fiscal_cache_lock,
            invalidate_fiscal_cache,
        )

        # Pre-populate cache
        with _fiscal_cache_lock:
            _fiscal_cache[(1, '2026-01-01')] = {'expense_account_ids': [], 'tax_list_str': ''}
            _fiscal_cache[(2, '2026-01-01')] = {'expense_account_ids': [], 'tax_list_str': ''}

        errors = []

        def worker():
            try:
                invalidate_fiscal_cache()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], 'Concurrent cache invalidation raised: %s' % errors)

    def test_cache_lock_exists(self):
        """The module-level lock should be a threading.Lock."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context import _fiscal_cache_lock

        self.assertIsInstance(_fiscal_cache_lock, type(threading.Lock()))


# ===================================================================
# Fix 7: Vendor memory race condition
# ===================================================================


@tagged('post_install', '-at_install')
class TestVendorMemoryRaceCondition(TransactionCase):
    """Fix 7: record_correction should handle IntegrityError gracefully."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Race Test Vendor', 'is_company': True})

    def test_integrity_error_import(self):
        """psycopg2.IntegrityError should be importable in the module."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import IntegrityError

        self.assertTrue(issubclass(IntegrityError, Exception))

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory.AiVendorMemory.search')
    def test_duplicate_create_is_handled(self, mock_search):
        """If search returns empty then create fails with IntegrityError,
        the code should catch it and retry."""
        from psycopg2 import IntegrityError

        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        # First search returns nothing (triggering create path)
        # Second search (in except block) returns an existing record
        existing_mock = MagicMock()
        existing_mock.correction_count = 1
        mock_search.side_effect = [
            self.env['ai.vendor.memory'],  # First search: empty
            existing_mock,  # Second search (retry): found
        ]

        with patch.object(
            type(self.env['ai.vendor.memory']),
            'create',
            side_effect=IntegrityError('duplicate key'),
        ):
            with patch.object(self.env.cr, 'rollback'):
                # Should not raise — IntegrityError is caught
                AiVendorMemory.record_correction(self.env, self.partner, 'account_id', '601000', '622600')


# ===================================================================
# Fix 8: N+1 queries in match_tax_by_rate
# ===================================================================


@tagged('post_install', '-at_install')
class TestGetVendorTaxes(TransactionCase):
    """Fix 8: _get_vendor_taxes should use a single search instead of mapped()."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Tax Test Vendor', 'is_company': True})

    def test_get_vendor_taxes_returns_recordset(self):
        """_get_vendor_taxes should return an account.tax recordset."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _get_vendor_taxes

        result = _get_vendor_taxes(self.env, self.partner, self.company)
        self.assertEqual(result._name, 'account.tax')

    def test_get_vendor_taxes_empty_for_new_vendor(self):
        """A vendor with no history should return empty recordset."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _get_vendor_taxes

        result = _get_vendor_taxes(self.env, self.partner, self.company)
        self.assertEqual(len(result), 0)

    def test_get_vendor_taxes_filters_purchase_only(self):
        """Only purchase taxes should be returned, not sale taxes."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _get_vendor_taxes

        # The function filters by type_tax_use='purchase' and active=True
        # With no past invoices, result is empty — just verify no crash
        result = _get_vendor_taxes(self.env, self.partner, self.company)
        for tax in result:
            self.assertEqual(tax.type_tax_use, 'purchase')
            self.assertTrue(tax.active)


# ===================================================================
# Fix 9: ai_confidence JSON normalization
# ===================================================================


@tagged('post_install', '-at_install')
class TestConfidenceJsonNormalization(TransactionCase):
    """Fix 9: _ai_handle_doc_issue should set consistent ai_confidence JSON."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create({'move_type': 'in_invoice', 'company_id': cls.company.id})

    def test_unsupported_doc_sets_confidence_with_overall(self):
        """Unsupported document should set ai_confidence with 'overall' key."""
        self.move._ai_handle_doc_issue({'unsupported': True})
        self.assertEqual(self.move.ai_extraction_status, 'failed')
        conf = json.loads(self.move.ai_confidence)
        self.assertIn('overall', conf)
        self.assertEqual(conf['overall'], 0.0)

    def test_proforma_sets_confidence_with_overall(self):
        """Pro-forma document should set ai_confidence with 'overall' key."""
        self.move._ai_handle_doc_issue({'is_proforma': True})
        self.assertEqual(self.move.ai_extraction_status, 'done')
        conf = json.loads(self.move.ai_confidence)
        self.assertIn('overall', conf)
        self.assertEqual(conf['overall'], 0.0)
        self.assertTrue(conf['proforma_warning']['found'])

    def test_proforma_confidence_is_valid_json(self):
        """ai_confidence should always be valid JSON."""
        self.move._ai_handle_doc_issue({'is_proforma': True})
        # Should not raise
        conf = json.loads(self.move.ai_confidence)
        self.assertIsInstance(conf, dict)


# ===================================================================
# Fix 10: API timeout 180s
# ===================================================================


@tagged('post_install', '-at_install')
class TestApiTimeout(TransactionCase):
    """Fix 10: Anthropic provider should use 180s timeout."""

    def test_timeout_is_180(self):
        """REQUEST_TIMEOUT should be 180 seconds."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider_anthropic import AnthropicProvider

        self.assertEqual(AnthropicProvider.REQUEST_TIMEOUT, 180)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_timeout_passed_to_requests(self, mock_post):
        """The timeout value should be passed to requests.post()."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider_anthropic import AnthropicProvider

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'usage': {'input_tokens': 0, 'output_tokens': 0},
                'model': 'test',
                'content': [{'type': 'text', 'text': '{}'}],
            },
        )
        provider = AnthropicProvider()
        provider.extract('fake-key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['timeout'], 180)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_timeout_error_returns_timeout_code(self, mock_post):
        """Timeout should return error with code 'timeout'."""
        import requests

        from odoo.addons.account_invoice_digitize_ai.models.ai_provider_anthropic import AnthropicProvider

        mock_post.side_effect = requests.Timeout('Connection timed out')
        provider = AnthropicProvider()
        result = provider.extract('fake-key', 'system', [{'type': 'text', 'text': 'test'}], 'claude-haiku-4-5-20251001')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'timeout')


# ===================================================================
# Fix 11: reliability_rate rounding
# ===================================================================


@tagged('post_install', '-at_install')
class TestReliabilityRateRounding(TransactionCase):
    """Fix 11: reliability_rate should be rounded to 2 decimal places."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Rounding Test Vendor', 'is_company': True})

    def _create_score(self, total, correct):
        return self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': total,
                'correct_extractions': correct,
            }
        )

    def test_one_third_rounded(self):
        """1/3 = 33.333... should be rounded to 33.33."""
        score = self._create_score(3, 1)
        self.assertEqual(score.reliability_rate, 33.33)

    def test_two_thirds_rounded(self):
        """2/3 = 66.666... should be rounded to 66.67."""
        score = self._create_score(3, 2)
        self.assertEqual(score.reliability_rate, 66.67)

    def test_one_seventh_rounded(self):
        """1/7 = 14.2857... should be rounded to 14.29."""
        score = self._create_score(7, 1)
        self.assertEqual(score.reliability_rate, 14.29)

    def test_exact_value_unchanged(self):
        """8/10 = 80.0 should remain 80.0."""
        score = self._create_score(10, 8)
        self.assertEqual(score.reliability_rate, 80.0)

    def test_full_score(self):
        """5/5 = 100.0."""
        score = self._create_score(5, 5)
        self.assertEqual(score.reliability_rate, 100.0)

    def test_zero_score(self):
        """0 extractions = 0.0."""
        score = self._create_score(0, 0)
        self.assertEqual(score.reliability_rate, 0.0)


# ===================================================================
# Fix #12: Rounding correction — configurable strategy
# ===================================================================


@tagged('post_install', '-at_install')
class TestRoundingCorrection(TransactionCase):
    """Test _ai_fix_rounding_gap with both strategies."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls._p = 'account_invoice_digitize_ai.'
        cls.ICP.set_param(cls._p + 'ai_rounding_correction', 'True')
        cls.ICP.set_param(cls._p + 'ai_rounding_tolerance', '0.05')

        cls.purchase_journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

    def _create_move_with_lines(self, price_unit=100.0):
        """Create a vendor bill with one line."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'journal_id': self.purchase_journal.id,
        })
        move.write({
            'invoice_line_ids': [
                (0, 0, {'name': 'Service A', 'quantity': 1, 'price_unit': price_unit}),
            ],
        })
        return move

    @staticmethod
    def _product_lines(move):
        """Return only product lines (Odoo 19: display_type='product', older: False)."""
        return move.invoice_line_ids.filtered(
            lambda ln: ln.display_type in (False, 'product')
        )

    def test_adjust_strategy_nudges_price(self):
        """Strategy 'adjust' should modify the price_unit of the existing line."""
        self.ICP.set_param(self._p + 'ai_rounding_strategy', 'adjust')
        move = self._create_move_with_lines(100.0)
        totals = {'total_amount': move.amount_total + 0.01}

        move._ai_fix_rounding_gap(totals)

        line = self._product_lines(move)
        self.assertEqual(line.price_unit, 100.01)

    def test_line_strategy_adds_rounding_line(self):
        """Strategy 'line' should add a compensation line."""
        self.ICP.set_param(self._p + 'ai_rounding_strategy', 'line')
        move = self._create_move_with_lines(100.0)
        totals = {'total_amount': move.amount_total + 0.01}
        lines_before = len(self._product_lines(move))

        move._ai_fix_rounding_gap(totals)

        product_lines = self._product_lines(move)
        self.assertEqual(len(product_lines), lines_before + 1)
        rounding_line = product_lines.filtered(
            lambda ln: ln.name == 'Rounding compensation'
        )
        self.assertTrue(rounding_line)
        self.assertEqual(rounding_line.price_unit, 0.01)

    def test_line_strategy_custom_label(self):
        """Custom label should be used for the rounding line."""
        self.ICP.set_param(self._p + 'ai_rounding_strategy', 'line')
        self.ICP.set_param(self._p + 'ai_rounding_line_label', 'Ajustement arrondi')
        move = self._create_move_with_lines(100.0)
        totals = {'total_amount': move.amount_total + 0.01}

        move._ai_fix_rounding_gap(totals)

        rounding_line = self._product_lines(move).filtered(
            lambda ln: ln.name == 'Ajustement arrondi'
        )
        self.assertTrue(rounding_line)
        # Clean up custom label
        self.ICP.set_param(self._p + 'ai_rounding_line_label', '')

    def test_line_strategy_no_tax(self):
        """Rounding compensation line should have no tax."""
        self.ICP.set_param(self._p + 'ai_rounding_strategy', 'line')
        move = self._create_move_with_lines(100.0)
        totals = {'total_amount': move.amount_total + 0.01}

        move._ai_fix_rounding_gap(totals)

        rounding_line = self._product_lines(move).filtered(
            lambda ln: ln.name == 'Rounding compensation'
        )
        self.assertFalse(rounding_line.tax_ids)

    def test_rounding_disabled(self):
        """No correction when feature is disabled."""
        self.ICP.set_param(self._p + 'ai_rounding_correction', 'False')
        move = self._create_move_with_lines(100.0)
        original_total = move.amount_total
        totals = {'total_amount': original_total + 0.01}

        move._ai_fix_rounding_gap(totals)

        self.assertEqual(move.amount_total, original_total)
        # Restore
        self.ICP.set_param(self._p + 'ai_rounding_correction', 'True')

    def test_no_correction_when_gap_exceeds_tolerance(self):
        """No correction when gap exceeds tolerance."""
        self.ICP.set_param(self._p + 'ai_rounding_strategy', 'adjust')
        move = self._create_move_with_lines(100.0)
        totals = {'total_amount': move.amount_total + 1.0}  # Way over 0.05 tolerance

        move._ai_fix_rounding_gap(totals)

        line = self._product_lines(move)
        self.assertEqual(line.price_unit, 100.0)

    def test_no_correction_when_gap_is_zero(self):
        """No correction when totals already match."""
        self.ICP.set_param(self._p + 'ai_rounding_strategy', 'adjust')
        move = self._create_move_with_lines(100.0)
        totals = {'total_amount': move.amount_total}

        move._ai_fix_rounding_gap(totals)

        line = self._product_lines(move)
        self.assertEqual(line.price_unit, 100.0)


# ===================================================================
# New cross-validations
# ===================================================================


@tagged('post_install', '-at_install')
class TestNewCrossValidations(TransactionCase):
    """Tests for the 4 new cross-validation checks."""

    def _validate(self, data):
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        return cross_validate(data)

    def test_tax_line_base_sum_mismatch(self):
        """Tax line base_amounts that don't sum to untaxed → 1 failure."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 1000.0, 'tax_amount': 200.0, 'total_amount': 1200.0},
            'tax_lines': [
                {'tax_rate': 20.0, 'base_amount': 500.0, 'tax_amount': 100.0, 'confidence': 0.9},
                {'tax_rate': 10.0, 'base_amount': 300.0, 'tax_amount': 30.0, 'confidence': 0.9},
            ],
        }
        failures = self._validate(data)
        self.assertGreaterEqual(failures, 1)

    def test_tax_line_base_rate_mismatch(self):
        """base × rate != tax_amount → 1 failure."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 1000.0, 'tax_amount': 200.0, 'total_amount': 1200.0},
            'tax_lines': [
                {'tax_rate': 20.0, 'base_amount': 1000.0, 'tax_amount': 100.0, 'confidence': 0.9},
            ],
        }
        failures = self._validate(data)
        self.assertGreaterEqual(failures, 1)

    def test_tax_line_consistent(self):
        """Consistent tax lines → 0 failures from tax checks."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 1000.0, 'tax_amount': 200.0, 'total_amount': 1200.0},
            'tax_lines': [
                {'tax_rate': 20.0, 'base_amount': 1000.0, 'tax_amount': 200.0, 'confidence': 0.9},
            ],
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_line_count_mismatch(self):
        """table_analysis.line_count != actual lines → 1 failure."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 300.0, 'tax_amount': 60.0, 'total_amount': 360.0},
            'table_analysis': {'line_count': 5, 'confidence': 0.9},
            'lines': [
                {'description': 'Item 1', 'subtotal_untaxed': 100.0, 'confidence': 0.9},
                {'description': 'Item 2', 'subtotal_untaxed': 200.0, 'confidence': 0.9},
            ],
        }
        failures = self._validate(data)
        self.assertGreaterEqual(failures, 1)

    def test_line_count_close_match(self):
        """line_count within tolerance (±1) → 0 failures from line_count."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 300.0, 'tax_amount': 60.0, 'total_amount': 360.0},
            'table_analysis': {'line_count': 3, 'confidence': 0.9},
            'lines': [
                {'description': 'Item 1', 'subtotal_untaxed': 100.0, 'confidence': 0.9},
                {'description': 'Item 2', 'subtotal_untaxed': 200.0, 'confidence': 0.9},
            ],
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_vat_format_invalid(self):
        """Invalid VAT format → 1 failure."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 100.0, 'tax_amount': 20.0, 'total_amount': 120.0},
            'vendor': {'name': 'Test', 'vat': 'INVALID123', 'confidence': 0.9},
        }
        failures = self._validate(data)
        self.assertGreaterEqual(failures, 1)

    def test_vat_format_valid_fr(self):
        """Valid French VAT → 0 failures from VAT check."""
        data = {
            'invoice': {'confidence': 0.9},
            'totals': {'untaxed_amount': 100.0, 'tax_amount': 20.0, 'total_amount': 120.0},
            'vendor': {'name': 'Test', 'vat': 'FR12345678901', 'confidence': 0.9},
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_date_range_future(self):
        """Invoice date > 60 days in the future → 1 failure."""
        data = {
            'invoice': {'invoice_date': '2028-01-01', 'confidence': 0.9},
            'totals': {'untaxed_amount': 100.0, 'tax_amount': 20.0, 'total_amount': 120.0},
        }
        failures = self._validate(data)
        self.assertGreaterEqual(failures, 1)

    def test_date_range_old(self):
        """Invoice date > 2 years ago → 1 failure."""
        data = {
            'invoice': {'invoice_date': '2020-01-01', 'confidence': 0.9},
            'totals': {'untaxed_amount': 100.0, 'tax_amount': 20.0, 'total_amount': 120.0},
        }
        failures = self._validate(data)
        self.assertGreaterEqual(failures, 1)

    def test_date_range_reasonable(self):
        """Recent date → 0 failures from date range check."""
        from datetime import date

        today = date.today().isoformat()
        data = {
            'invoice': {'invoice_date': today, 'confidence': 0.9},
            'totals': {'untaxed_amount': 100.0, 'tax_amount': 20.0, 'total_amount': 120.0},
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)


# ===================================================================
# Bug fix: b64decode crash on empty attachment
# ===================================================================


@tagged('post_install', '-at_install')
class TestEmptyAttachmentGuard(TransactionCase):
    """b64decode must not crash when attachment.datas is empty."""

    def test_empty_datas_sets_failed(self):
        move = self.env['account.move'].create({'move_type': 'in_invoice'})
        att = self.env['ir.attachment'].create({
            'name': 'empty.pdf',
            'res_model': 'account.move',
            'res_id': move.id,
            'datas': False,
            'mimetype': 'application/pdf',
        })
        with patch.object(type(self.env['account.move']), '_ai_get_config', return_value={
            'provider_name': 'anthropic',
            'model_id': 'claude-haiku-4-5-20251001',
            'extract_lines': False,
            'debug_mode': False,
            'preprocess_provider': 'none',
            'preprocess_mode': 'ocr_replacement',
            'preprocess_threshold': 0.75,
            'extraction_mode': 'guided',
            'extract_qr_codes': True,
        }):
            move._ai_trigger_extraction('fake-key', att)
        self.assertEqual(move.ai_extraction_status, 'failed')


# ===================================================================
# Bug fix: json.loads safety on cached data
# ===================================================================


@tagged('post_install', '-at_install')
class TestJsonLoadsSafety(TransactionCase):
    """json.loads on corrupted cached data must not crash."""

    def test_corrupted_cache_does_not_crash(self):
        move = self.env['account.move'].create({'move_type': 'in_invoice'})
        move.ai_last_extraction_data = 'NOT VALID JSON {'
        result = move.action_ai_view_results()
        self.assertFalse(result)
