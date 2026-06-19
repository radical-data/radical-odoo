"""Tests for fiscal context builder and caching."""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFiscalContextCache(TransactionCase):
    """Fiscal context: cache build, invalidation, and daily expiry."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.from_mod = (
            'odoo.addons.account_invoice_digitize_ai.models.ai_fiscal_context'
        )

    def _get_module(self):
        from odoo.addons.account_invoice_digitize_ai.models import ai_fiscal_context
        return ai_fiscal_context

    def setUp(self):
        super().setUp()
        # Always start with a clean cache
        mod = self._get_module()
        mod.invalidate_fiscal_cache()

    def test_cache_populates_on_first_call(self):
        """First call to build_fiscal_context should populate the cache."""
        mod = self._get_module()
        result = mod.build_fiscal_context(self.env, self.company)
        self.assertTrue(result)
        # Cache should now be populated for this company
        with mod._fiscal_cache_lock:
            keys = [k for k in mod._fiscal_cache if k[0] == self.company.id]
        self.assertEqual(len(keys), 1)

    def test_cache_returns_same_data(self):
        """Second call should return cached data without extra queries."""
        mod = self._get_module()
        result1 = mod.build_fiscal_context(self.env, self.company)
        result2 = mod.build_fiscal_context(self.env, self.company)
        # Same content (cache hit)
        self.assertEqual(result1, result2)

    def test_invalidate_clears_company(self):
        """invalidate_fiscal_cache(company_id) should only clear that company."""
        mod = self._get_module()
        # Populate cache
        mod.build_fiscal_context(self.env, self.company)
        # Add a fake entry for another company
        with mod._fiscal_cache_lock:
            mod._fiscal_cache[(9999, '2026-01-01')] = {
                'expense_account_ids': [],
                'tax_list_str': '',
            }

        mod.invalidate_fiscal_cache(self.company.id)

        with mod._fiscal_cache_lock:
            own_keys = [k for k in mod._fiscal_cache if k[0] == self.company.id]
            other_keys = [k for k in mod._fiscal_cache if k[0] == 9999]
        self.assertEqual(len(own_keys), 0)
        self.assertEqual(len(other_keys), 1)

    def test_invalidate_all(self):
        """invalidate_fiscal_cache() with no arg should clear everything."""
        mod = self._get_module()
        mod.build_fiscal_context(self.env, self.company)
        with mod._fiscal_cache_lock:
            mod._fiscal_cache[(9999, '2026-01-01')] = {
                'expense_account_ids': [],
                'tax_list_str': '',
            }

        mod.invalidate_fiscal_cache()

        with mod._fiscal_cache_lock:
            self.assertEqual(len(mod._fiscal_cache), 0)

    def test_stale_day_evicted_on_rebuild(self):
        """Cache entries from a previous day should be evicted on rebuild."""
        mod = self._get_module()
        # Seed a stale entry with yesterday's date
        with mod._fiscal_cache_lock:
            mod._fiscal_cache[(self.company.id, '2020-01-01')] = {
                'expense_account_ids': [],
                'tax_list_str': '',
            }

        # Rebuild — should evict stale + create today's
        mod.build_fiscal_context(self.env, self.company)

        with mod._fiscal_cache_lock:
            keys = [k for k in mod._fiscal_cache if k[0] == self.company.id]
        self.assertEqual(len(keys), 1)
        self.assertNotEqual(keys[0][1], '2020-01-01')

    def test_tax_only_mode(self):
        """include_accounts=False should produce a shorter result."""
        mod = self._get_module()
        full = mod.build_fiscal_context(self.env, self.company, include_accounts=True)
        tax_only = mod.build_fiscal_context(self.env, self.company, include_accounts=False)
        self.assertTrue(len(tax_only) < len(full))
