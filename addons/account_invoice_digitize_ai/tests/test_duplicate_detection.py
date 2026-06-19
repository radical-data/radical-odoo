from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDuplicateDetection(TransactionCase):
    """Test duplicate invoice detection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Duplicate Test Vendor',
                'is_company': True,
            }
        )
        # Create an existing posted vendor bill
        cls.existing = cls.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': cls.partner.id,
                'ref': 'INV-2024-001',
                'invoice_date': '2024-06-15',
            }
        )

    def _create_draft_bill(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
            }
        )

    def test_exact_duplicate_detected(self):
        """Exact match on partner + ref + date + amount flags duplicate."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_duplicate_detector,
        )

        new = self._create_draft_bill()
        result = ai_duplicate_detector.detect_duplicates(
            self.env, new, self.partner, 'INV-2024-001', '2024-06-15', self.existing.amount_total
        )
        self.assertTrue(result.get('found'))
        self.assertEqual(result['severity'], 'exact')
        self.assertIn(self.existing.id, result['duplicate_ids'])

    def test_partial_match_detected(self):
        """Partner + ref match (different date/amount) flags partial."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_duplicate_detector,
        )

        new = self._create_draft_bill()
        result = ai_duplicate_detector.detect_duplicates(
            self.env, new, self.partner, 'INV-2024-001', '2024-12-01', 9999.99
        )
        self.assertTrue(result.get('found'))
        self.assertEqual(result['severity'], 'partial')

    def test_no_duplicate_different_ref(self):
        """Different reference should not flag duplicate."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_duplicate_detector,
        )

        new = self._create_draft_bill()
        result = ai_duplicate_detector.detect_duplicates(
            self.env, new, self.partner, 'COMPLETELY-DIFFERENT', '2024-06-15', self.existing.amount_total
        )
        self.assertFalse(result.get('found'))

    def test_no_check_without_ref(self):
        """No ref means skip duplicate check entirely."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_duplicate_detector,
        )

        new = self._create_draft_bill()
        result = ai_duplicate_detector.detect_duplicates(self.env, new, self.partner, None, '2024-06-15', 100.0)
        self.assertEqual(result, {})

    def test_no_check_without_partner(self):
        """No partner means skip duplicate check entirely."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_duplicate_detector,
        )

        new = self._create_draft_bill()
        result = ai_duplicate_detector.detect_duplicates(self.env, new, None, 'INV-2024-001', '2024-06-15', 100.0)
        self.assertEqual(result, {})

    def test_self_exclusion(self):
        """Current invoice should not match itself."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_duplicate_detector,
        )

        result = ai_duplicate_detector.detect_duplicates(
            self.env, self.existing, self.partner, 'INV-2024-001', '2024-06-15', self.existing.amount_total
        )
        self.assertFalse(result.get('found'))
