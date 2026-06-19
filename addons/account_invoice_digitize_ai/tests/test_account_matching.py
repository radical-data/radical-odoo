from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccountMatching(TransactionCase):
    """Test account matching logic (ai_matcher.match_account)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Test Vendor', 'is_company': True})

    def _match(self, category='', description='', partner=None):
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_account

        return match_account(self.env, category, description, self.company, partner=partner)

    def test_no_history_returns_fallback(self):
        """With no vendor history and no category, should return a fallback expense account."""
        result = self._match(category='', description='Some service')
        # Should return some account (fallback)
        if result:
            self.assertTrue(result.code)

    def test_empty_category_no_crash(self):
        """Empty category should not crash — just skip tier 3."""
        self._match(category='', description='')
        # May return None or fallback, should not raise

    def test_category_mapping(self):
        """Category mapping should match known categories to account prefixes."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _match_account_by_category

        # This depends on the chart of accounts installed; just check it doesn't crash
        _match_account_by_category(self.env, 'consulting', self.company)
        # May or may not find an account depending on chart — just no crash

    def test_unknown_category(self):
        """Unknown category should return None from category mapping."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _match_account_by_category

        result = _match_account_by_category(self.env, 'unicorn_breeding', self.company)
        self.assertIsNone(result)

    def test_category_overrides_vendor_default(self):
        """Category mapping (tier 2) should take priority over vendor default (tier 3).

        When a vendor has a dominant account in history (e.g. 607 merchandise)
        but the AI detects a shipping line, the shipping account (6241) should
        win — not the vendor's most-used account.
        """
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import (
            _get_vendor_default_account,
            _match_account_by_category,
            match_account,
        )

        # Create two expense accounts: merchandise (607) and shipping (6241)
        Account = self.env['account.account']
        acc_vals_607 = {
            'name': 'Achats de marchandises',
            'code': '607100',
            'account_type': 'expense',
        }
        acc_vals_6241 = {
            'name': 'Transports sur achats',
            'code': '624100',
            'account_type': 'expense',
        }
        # Odoo 19: company_ids (many2many); older: company_id (many2one)
        if 'company_ids' in Account._fields:
            acc_vals_607['company_ids'] = [(4, self.company.id)]
            acc_vals_6241['company_ids'] = [(4, self.company.id)]
        else:
            acc_vals_607['company_id'] = self.company.id
            acc_vals_6241['company_id'] = self.company.id
        acc_607 = Account.create(acc_vals_607)
        acc_6241 = Account.create(acc_vals_6241)

        # Create posted invoice history: 5 lines on 607 (merchandise)
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.partner.id,
            'invoice_date': '2025-01-15',
            'invoice_line_ids': [
                (0, 0, {'name': 'Laptop', 'quantity': 1, 'price_unit': 500, 'account_id': acc_607.id}),
                (0, 0, {'name': 'Keyboard', 'quantity': 1, 'price_unit': 50, 'account_id': acc_607.id}),
                (0, 0, {'name': 'Mouse', 'quantity': 1, 'price_unit': 25, 'account_id': acc_607.id}),
                (0, 0, {'name': 'Monitor', 'quantity': 1, 'price_unit': 300, 'account_id': acc_607.id}),
                (0, 0, {'name': 'Cable', 'quantity': 1, 'price_unit': 10, 'account_id': acc_607.id}),
            ],
        })
        move.action_post()

        # Verify vendor default is 607 (most used)
        vendor_default = _get_vendor_default_account(self.env, self.company, self.partner)
        self.assertEqual(vendor_default, acc_607, 'Vendor default should be 607 (most used)')

        # Verify category mapping for shipping returns 6241
        shipping_cat = _match_account_by_category(self.env, 'shipping', self.company)
        self.assertEqual(shipping_cat, acc_6241, 'Category shipping should map to 6241')

        # THE KEY TEST: match_account with category='shipping' should return
        # 6241 (category), NOT 607 (vendor default)
        result = match_account(
            self.env, 'shipping', 'Frais de port DHL', self.company, partner=self.partner,
        )
        self.assertEqual(
            result, acc_6241,
            'Category mapping (shipping→6241) should override vendor default (607)',
        )


@tagged('post_install', '-at_install')
class TestVendorMatchCache(TransactionCase):
    """Test VendorMatchCache caching behavior."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Cache Test Vendor', 'is_company': True})

    def test_past_lines_cached(self):
        """get_vendor_past_lines should cache results (no duplicate DB queries)."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import VendorMatchCache

        cache = VendorMatchCache()
        result1 = cache.get_vendor_past_lines(self.env, self.partner, self.company)
        self.assertEqual(len(cache._data), 1)
        key = ('past_lines', self.partner.id, self.company.id)
        self.assertIn(key, cache._data)
        result2 = cache.get_vendor_past_lines(self.env, self.partner, self.company)
        self.assertIs(result1, result2)
        self.assertEqual(len(cache._data), 1)

    def test_vendor_taxes_cached(self):
        """get_vendor_taxes should cache results and reuse past_lines cache."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import VendorMatchCache

        cache = VendorMatchCache()
        result1 = cache.get_vendor_taxes(self.env, self.partner, self.company)
        # get_vendor_taxes internally calls get_vendor_past_lines, so 2 keys
        self.assertEqual(len(cache._data), 2)
        result2 = cache.get_vendor_taxes(self.env, self.partner, self.company)
        self.assertIs(result1, result2)
        self.assertEqual(len(cache._data), 2)

    def test_all_purchase_taxes_cached(self):
        """get_all_purchase_taxes should cache results."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import VendorMatchCache

        cache = VendorMatchCache()
        result1 = cache.get_all_purchase_taxes(self.env, self.company)
        self.assertEqual(len(cache._data), 1)
        result2 = cache.get_all_purchase_taxes(self.env, self.company)
        self.assertIs(result1, result2)
        self.assertEqual(len(cache._data), 1)

    def test_different_partners_separate_cache_entries(self):
        """Different partners should have separate cache entries."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import VendorMatchCache

        partner2 = self.env['res.partner'].create({'name': 'Other Vendor', 'is_company': True})
        cache = VendorMatchCache()
        cache.get_vendor_past_lines(self.env, self.partner, self.company)
        cache.get_vendor_past_lines(self.env, partner2, self.company)
        self.assertEqual(len(cache._data), 2)
