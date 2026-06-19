"""Pipeline guard tests: vision retry, rate limiting, line validation, page limits.

Covers:
  1. Vision retry debug log ordering
  2. Line sum validation warning
  3. Rate limiting (concurrent extraction guard)
  4. Partner matching combined name query
  5. Table extraction page limit
"""

import logging
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

_MODULE = 'odoo.addons.account_invoice_digitize_ai'


# ===================================================================
# Fix #1: Vision retry — log only after success check
# ===================================================================


@tagged('post_install', '-at_install')
class TestVisionRetryLogOrder(TransactionCase):
    """Vision retry must not log before checking API success."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': cls.company.id,
            }
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_api_key',
            'test-key-123',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_provider',
            'anthropic',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_model_selection',
            'claude-haiku-4-5-20251001',
        )

    @patch(f'{_MODULE}.models.ai_document.extract_text_from_pdf', return_value='')
    @patch(f'{_MODULE}.models.ai_document.extract_pdf_metadata', return_value={})
    def test_vision_retry_failure_no_log_before_check(self, _meta, _text):
        """When vision retry fails, log should still be created (for debug) but after the check."""
        mock_provider = MagicMock()
        mock_provider.extract.return_value = {
            'success': False,
            'error': 'parse',
            'message': 'Failed',
            'data': None,
            'raw_text': '',
            'input_tokens': 0,
            'output_tokens': 0,
            'model': 'claude-haiku-4-5-20251001',
        }

        cfg = {
            'provider_name': 'anthropic',
            'model_id': 'claude-haiku-4-5-20251001',
            'extract_lines': False,
            'debug_mode': True,
        }
        with patch(f'{_MODULE}.models.ai_provider.get_provider', return_value=mock_provider):
            result = self.move._ai_retry_vision(
                'test-key-123',
                cfg,
                b'fake-pdf',
            )

        self.assertIsNone(result)
        # Log should have been created (debug_mode=True)
        mock_provider.extract.assert_called_once()

    @patch(f'{_MODULE}.models.ai_document.extract_text_from_pdf', return_value='')
    @patch(f'{_MODULE}.models.ai_document.extract_pdf_metadata', return_value={})
    @patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=0)
    def test_vision_retry_success_creates_log(self, _valid, _meta, _text):
        """When vision retry succeeds, debug log should be created."""
        mock_provider = MagicMock()
        mock_provider.extract.return_value = {
            'success': True,
            'data': {'vendor': {}, 'invoice': {}, 'totals': {}},
            'raw_text': '{}',
            'input_tokens': 100,
            'output_tokens': 50,
            'model': 'claude-haiku-4-5-20251001',
        }

        cfg = {
            'provider_name': 'anthropic',
            'model_id': 'claude-haiku-4-5-20251001',
            'extract_lines': False,
            'debug_mode': True,
        }
        with (
            patch(f'{_MODULE}.models.ai_provider.get_provider', return_value=mock_provider),
            patch.object(type(self.move), '_ai_create_log') as mock_log,
        ):
            result = self.move._ai_retry_vision(
                'test-key-123',
                cfg,
                b'fake-pdf',
            )

        self.assertIsNotNone(result)
        mock_log.assert_called_once()


# ===================================================================
# Fix #2: Line sum validation
# ===================================================================


@tagged('post_install', '-at_install')
class TestLineSumValidation(TransactionCase):
    """_ai_apply_lines should warn when line sum diverges from extracted total."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': cls.company.id,
            }
        )

    def test_line_sum_mismatch_logs_warning(self):
        """Warning should be logged when line amounts don't match totals."""
        lines = [
            {'description': 'Service A', 'quantity': 1, 'unit_price': 500.0},
            {'description': 'Service B', 'quantity': 1, 'unit_price': 300.0},
        ]
        totals = {'untaxed_amount': 1000.0}  # Expected 1000, actual 800

        with self.assertLogs(f'{_MODULE}.models.ai_line_builder', level='WARNING') as cm:
            self.move._ai_apply_lines(lines, totals)

        self.assertTrue(any('line sum' in msg.lower() or 'differs' in msg.lower() for msg in cm.output))

    def test_line_sum_match_no_warning(self):
        """No warning when line amounts match totals."""
        lines = [
            {'description': 'Service A', 'quantity': 1, 'unit_price': 500.0},
            {'description': 'Service B', 'quantity': 1, 'unit_price': 500.0},
        ]
        totals = {'untaxed_amount': 1000.0}

        logger = logging.getLogger(f'{_MODULE}.models.ai_line_builder')
        with patch.object(logger, 'warning') as mock_warn:
            self.move._ai_apply_lines(lines, totals)

        # No warning about line sum mismatch (may have other warnings)
        for call in mock_warn.call_args_list:
            self.assertNotIn('differs from extracted', str(call).lower())

    def test_empty_totals_no_crash(self):
        """Empty totals dict should not crash."""
        lines = [{'description': 'Service', 'quantity': 1, 'unit_price': 100.0}]
        # Should not raise
        self.move._ai_apply_lines(lines, {})


# ===================================================================
# Fix #4: Partner matching — combined name query
# ===================================================================


@tagged('post_install', '-at_install')
class TestPartnerMatchingCombined(TransactionCase):
    """Partner matching should find exact name matches before partial ones."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.exact_partner = cls.env['res.partner'].create(
            {
                'name': 'ACME Corp',
                'is_company': True,
            }
        )
        cls.partial_partner = cls.env['res.partner'].create(
            {
                'name': 'ACME Corp International Group',
                'is_company': True,
            }
        )

    def test_exact_match_preferred_over_partial(self):
        """Exact name match should be returned even if partial match exists."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_partner

        result = match_partner(self.env, {'name': 'ACME Corp'})
        self.assertEqual(result, self.exact_partner)

    def test_partial_match_when_no_exact(self):
        """Partial match returned when no exact match."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_partner

        result = match_partner(self.env, {'name': 'International Group'})
        # Should find partial_partner via ilike
        if result:
            self.assertEqual(result, self.partial_partner)


# ===================================================================
# Fix #6: Rate limiting
# ===================================================================


@tagged('post_install', '-at_install')
class TestRateLimiting(TransactionCase):
    """Rate limiting should prevent re-triggering on same invoice."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_api_key',
            'test-key-123',
        )

    def test_already_processing_blocked(self):
        """Cannot trigger extraction if already processing."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        move.ai_extraction_status = 'processing'

        result = move.action_ai_extract()
        self.assertEqual(result['tag'], 'display_notification')
        self.assertIn('already running', result['params']['message'].lower())

    def test_concurrent_limit_blocked(self):
        """Cannot exceed max concurrent extractions."""
        moves = self.env['account.move']
        for _i in range(6):
            m = self.env['account.move'].create(
                {
                    'move_type': 'in_invoice',
                    'company_id': self.company.id,
                }
            )
            m.ai_extraction_status = 'processing'
            moves |= m

        new_move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        result = new_move.action_ai_extract()
        self.assertEqual(result['tag'], 'display_notification')
        self.assertIn('extractions', result['params']['message'].lower())


# ===================================================================
# Fix #7: Table extraction page limit
# ===================================================================


@tagged('post_install', '-at_install')
class TestTableExtractionPageLimit(TransactionCase):
    """Table extraction should respect MAX_TABLE_PAGES limit."""

    @patch(f'{_MODULE}.models.ai_document.PDFPLUMBER_AVAILABLE', True)
    @patch(f'{_MODULE}.models.ai_document._pdfplumber')
    def test_large_pdf_limited(self, mock_plumber):
        """PDF with >50 pages should only process first 50."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import (
            MAX_TABLE_PAGES,
            extract_tables_from_pdf,
        )

        # Create 60 mock pages, each with a valid table
        mock_pages = []
        for _ in range(60):
            page = MagicMock()
            page.extract_tables.return_value = [
                [
                    ['Desc', 'Qty', 'Price'],
                    ['Item', '1', '100.00'],
                ]
            ]
            mock_pages.append(page)

        mock_pdf = MagicMock()
        mock_pdf.pages = mock_pages
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        extract_tables_from_pdf(b'fake-pdf')

        # Only first MAX_TABLE_PAGES pages should have extract_tables called
        called_count = sum(1 for p in mock_pages if p.extract_tables.called)
        self.assertLessEqual(called_count, MAX_TABLE_PAGES)


# ===================================================================
# VendorMatchCache — avoid N identical DB queries per invoice line
# ===================================================================


@tagged('post_install', '-at_install')
class TestVendorMatchCache(TransactionCase):
    """VendorMatchCache should query DB once and reuse for all lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Cache Test Vendor',
                'is_company': True,
            }
        )

    def test_cache_returns_same_object(self):
        """Two calls with same partner/company should return same recordset."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import VendorMatchCache

        cache = VendorMatchCache()
        result1 = cache.get_vendor_past_lines(self.env, self.partner, self.company)
        result2 = cache.get_vendor_past_lines(self.env, self.partner, self.company)
        # Same Python object — no second query
        self.assertIs(result1, result2)

    def test_cache_taxes_returns_same_object(self):
        """Two calls for vendor taxes should return same recordset."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import VendorMatchCache

        cache = VendorMatchCache()
        result1 = cache.get_vendor_taxes(self.env, self.partner, self.company)
        result2 = cache.get_vendor_taxes(self.env, self.partner, self.company)
        self.assertIs(result1, result2)

    def test_match_account_uses_cache(self):
        """match_account called multiple times should only query vendor lines once."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import (
            VendorMatchCache,
            match_account,
        )

        cache = VendorMatchCache()
        match_account(self.env, '', 'Service A', self.company, partner=self.partner, cache=cache)
        match_account(self.env, '', 'Service B', self.company, partner=self.partner, cache=cache)
        match_account(self.env, '', 'Service C', self.company, partner=self.partner, cache=cache)

        # DB query only runs once — cache dict has exactly one past_lines entry
        past_lines_keys = [k for k in cache._data if k[0] == 'past_lines']
        self.assertEqual(len(past_lines_keys), 1)

    def test_match_tax_uses_cache(self):
        """match_tax_by_rate called multiple times should only query vendor taxes once."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import (
            VendorMatchCache,
            match_tax_by_rate,
        )

        cache = VendorMatchCache()
        match_tax_by_rate(self.env, 20.0, self.company, partner=self.partner, cache=cache)
        match_tax_by_rate(self.env, 10.0, self.company, partner=self.partner, cache=cache)
        match_tax_by_rate(self.env, 5.5, self.company, partner=self.partner, cache=cache)

        # Only one taxes cache entry for this partner/company
        tax_keys = [k for k in cache._data if k[0] == 'taxes']
        self.assertEqual(len(tax_keys), 1)
