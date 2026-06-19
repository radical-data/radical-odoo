from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAutoApply(TransactionCase):
    """Test auto-apply when confidence is high."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Reliable Vendor SAS',
                'is_company': True,
                'vat': 'FR12345678901',
            }
        )
        cls.company = cls.env.company
        # Enable auto-apply
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_auto_apply_enabled', 'True')
        ICP.set_param('account_invoice_digitize_ai.ai_auto_apply_min_confidence', '0.80')
        ICP.set_param('account_invoice_digitize_ai.ai_provider', 'anthropic')
        ICP.set_param('account_invoice_digitize_ai.ai_extraction_mode', 'guided')

        # Create a reliable vendor score (>= 3 extractions, >= 70% rate)
        cls.env['ai.vendor.score'].create(
            {
                'partner_id': cls.partner.id,
                'company_id': cls.company.id,
                'total_extractions': 10,
                'correct_extractions': 9,
            }
        )

        # Create test invoice
        cls.move = cls.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': cls.partner.id,
            }
        )

    def _make_high_confidence_data(self):
        return {
            'document_type': 'invoice',
            'vendor': {
                'name': 'Reliable Vendor SAS',
                'vat': 'FR12345678901',
                'confidence': 0.95,
            },
            'invoice': {
                'reference': 'INV-2024-001',
                'invoice_date': '2024-01-15',
                'confidence': 0.90,
            },
            'totals': {
                'total_amount': 1200.0,
                'untaxed_amount': 1000.0,
                'tax_amount': 200.0,
                'confidence': 0.92,
            },
        }

    def _make_low_confidence_data(self):
        data = self._make_high_confidence_data()
        data['vendor']['confidence'] = 0.40
        return data

    def test_auto_apply_high_confidence(self):
        """High confidence + reliable vendor → auto-apply returns True."""
        data = self._make_high_confidence_data()
        result = self.move._ai_can_auto_apply(data)
        self.assertTrue(result)

    def test_no_auto_apply_low_confidence(self):
        """Low vendor confidence → auto-apply returns False."""
        data = self._make_low_confidence_data()
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)

    def test_no_auto_apply_unknown_vendor(self):
        """Unknown vendor (no VAT match) → auto-apply returns False."""
        data = self._make_high_confidence_data()
        data['vendor']['vat'] = 'FR99999999999'
        data['vendor']['name'] = 'Unknown Company XYZZY'
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)

    def test_no_auto_apply_new_vendor(self):
        """New vendor with < 3 extractions → auto-apply returns False."""
        new_partner = self.env['res.partner'].create(
            {
                'name': 'New Vendor Ltd',
                'is_company': True,
                'vat': 'FR00000000001',
            }
        )
        self.env['ai.vendor.score'].create(
            {
                'partner_id': new_partner.id,
                'company_id': self.company.id,
                'total_extractions': 1,
                'correct_extractions': 1,
            }
        )
        data = self._make_high_confidence_data()
        data['vendor']['name'] = 'New Vendor Ltd'
        data['vendor']['vat'] = 'FR00000000001'
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)

    def test_no_auto_apply_when_disabled(self):
        """Auto-apply disabled in settings → returns False."""
        self.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_auto_apply_enabled',
            'False',
        )
        data = self._make_high_confidence_data()
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)
        # Re-enable for other tests
        self.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_auto_apply_enabled',
            'True',
        )

    def test_no_auto_apply_proforma(self):
        """Pro-forma document → auto-apply returns False."""
        data = self._make_high_confidence_data()
        data['document_type'] = 'proforma'
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)

    def test_no_auto_apply_marked_paid(self):
        """Document marked as paid → auto-apply returns False."""
        data = self._make_high_confidence_data()
        data['is_marked_paid'] = True
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)

    def test_no_auto_apply_low_totals_confidence(self):
        """Low totals confidence → auto-apply returns False."""
        data = self._make_high_confidence_data()
        data['totals']['confidence'] = 0.30
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)

    def test_no_auto_apply_low_invoice_confidence(self):
        """Low invoice confidence → auto-apply returns False."""
        data = self._make_high_confidence_data()
        data['invoice']['confidence'] = 0.30
        result = self.move._ai_can_auto_apply(data)
        self.assertFalse(result)
