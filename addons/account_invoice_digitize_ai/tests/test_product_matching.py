from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProductMatching(TransactionCase):
    """Test product matching logic (ai_matcher.match_product)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Test Vendor', 'is_company': True})

        # Create a product with default_code
        cls.product_a = cls.env['product.product'].create(
            {
                'name': 'Widget A',
                'default_code': 'WDG-001',
                'type': 'consu',
            }
        )

        # Create a product with supplier info
        cls.product_b = cls.env['product.product'].create(
            {
                'name': 'Widget B',
                'type': 'consu',
            }
        )
        cls.env['product.supplierinfo'].create(
            {
                'partner_id': cls.partner.id,
                'product_tmpl_id': cls.product_b.product_tmpl_id.id,
                'product_code': 'VENDOR-B-123',
                'price': 10.0,
            }
        )

        # Create supplier info for a different vendor
        cls.other_partner = cls.env['res.partner'].create({'name': 'Other Vendor', 'is_company': True})
        cls.product_c = cls.env['product.product'].create(
            {
                'name': 'Widget C',
                'type': 'consu',
            }
        )
        cls.env['product.supplierinfo'].create(
            {
                'partner_id': cls.other_partner.id,
                'product_tmpl_id': cls.product_c.product_tmpl_id.id,
                'product_code': 'OTHER-C-456',
                'price': 20.0,
            }
        )

    def _match(self, product_code, description='', partner=None):
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_product

        return match_product(self.env, product_code, description, partner=partner)

    def test_no_code_returns_none(self):
        """Empty or None product code returns None."""
        self.assertIsNone(self._match(None))
        self.assertIsNone(self._match(''))
        self.assertIsNone(self._match('   '))

    def test_match_by_supplier_info_vendor_specific(self):
        """Match via product.supplierinfo with vendor filter."""
        result = self._match('VENDOR-B-123', partner=self.partner)
        self.assertEqual(result, self.product_b)

    def test_match_by_supplier_info_any_vendor(self):
        """Match via product.supplierinfo without vendor filter (tier 2)."""
        result = self._match('OTHER-C-456')
        self.assertEqual(result, self.product_c)

    def test_match_by_default_code(self):
        """Match via product.product default_code (tier 3)."""
        result = self._match('WDG-001')
        self.assertEqual(result, self.product_a)

    def test_case_insensitive_match(self):
        """Product code matching should be case-insensitive."""
        result = self._match('wdg-001')
        self.assertEqual(result, self.product_a)

    def test_vendor_specific_takes_priority(self):
        """Vendor-specific supplier info match should come before generic match."""
        result = self._match('VENDOR-B-123', partner=self.partner)
        self.assertEqual(result, self.product_b)

    def test_no_match_returns_none(self):
        """Unknown product code returns None."""
        result = self._match('UNKNOWN-999', partner=self.partner)
        self.assertIsNone(result)

    def test_wrong_vendor_falls_through_to_any(self):
        """When vendor-specific search fails, falls through to any-vendor search."""
        # OTHER-C-456 belongs to other_partner, not self.partner
        result = self._match('OTHER-C-456', partner=self.partner)
        # Should still find it via tier 2 (any vendor)
        self.assertEqual(result, self.product_c)
