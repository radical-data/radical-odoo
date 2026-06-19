from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccountLearning(TransactionCase):
    """Test account-level learning via vendor memory."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Test Vendor SARL',
                'is_company': True,
            }
        )
        cls.company = cls.env.company
        # Set threshold to 3
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_auto_apply_threshold',
            '3',
        )
        # Find two expense accounts (Odoo 19: company_ids, older: company_id)
        Account = cls.env['account.account']
        if 'company_ids' in Account._fields:
            co_domain = ('company_ids', 'in', cls.company.id)
        else:
            co_domain = ('company_id', '=', cls.company.id)
        domain = [co_domain]
        # Try to get accounts by type
        try:
            domain.append(('account_type', 'in', ['expense', 'expense_direct_cost']))
        except Exception:
            pass
        accounts = Account.search(domain, limit=2)
        if len(accounts) < 2:
            # Fallback: any two accounts
            accounts = Account.search([co_domain], limit=2)
        cls.account_ai = accounts[0] if accounts else None
        cls.account_user = accounts[1] if len(accounts) > 1 else None

    def test_record_line_correction_creates_entry(self):
        """Recording a line correction should create a vendor memory entry."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        AiVendorMemory.record_line_correction(
            self.env,
            self.partner,
            'Office supplies and stationery',
            str(self.account_ai.id),
            str(self.account_user.id),
            company=self.company,
        )

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'account_id'),
                ('line_description', '!=', False),
            ]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.ai_value, str(self.account_ai.id))
        self.assertEqual(entry.user_value, str(self.account_user.id))
        self.assertEqual(entry.line_description, 'Office supplies and stationery')
        self.assertEqual(entry.correction_count, 1)
        self.assertFalse(entry.auto_apply)

    def test_line_correction_increments_count(self):
        """Repeated corrections for same description should increment count."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        for _ in range(2):
            AiVendorMemory.record_line_correction(
                self.env,
                self.partner,
                'Cloud hosting services',
                str(self.account_ai.id),
                str(self.account_user.id),
                company=self.company,
            )

        entries = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'account_id'),
                ('line_description', '!=', False),
            ]
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.correction_count, 2)

    def test_line_correction_auto_apply_threshold(self):
        """After reaching threshold, auto_apply should be True."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        for _ in range(3):
            AiVendorMemory.record_line_correction(
                self.env,
                self.partner,
                'Monthly subscription fee',
                str(self.account_ai.id),
                str(self.account_user.id),
                company=self.company,
            )

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'account_id'),
            ]
        )
        self.assertTrue(entry.auto_apply)
        self.assertEqual(entry.correction_count, 3)

    def test_account_override_applied(self):
        """After auto_apply is set, get_account_override should return the account."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        # Create an auto-apply entry
        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'company_id': self.company.id,
                'field_name': 'account_id',
                'ai_value': str(self.account_ai.id),
                'user_value': str(self.account_user.id),
                'line_description': 'Professional consulting services',
                'correction_count': 5,
                'auto_apply': True,
            }
        )

        # Similar description should match
        override = AiVendorMemory.get_account_override(
            self.env,
            self.partner,
            'Professional consulting services for Q1',
            company=self.company,
        )
        self.assertEqual(override, self.account_user.id)

    def test_account_override_description_mismatch(self):
        """Unrelated descriptions should not match."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'company_id': self.company.id,
                'field_name': 'account_id',
                'ai_value': str(self.account_ai.id),
                'user_value': str(self.account_user.id),
                'line_description': 'Professional consulting services',
                'correction_count': 5,
                'auto_apply': True,
            }
        )

        # Completely different description
        override = AiVendorMemory.get_account_override(
            self.env,
            self.partner,
            'Electricity bill for warehouse',
            company=self.company,
        )
        self.assertIsNone(override)

    def test_no_override_below_threshold(self):
        """Entries below threshold should not be returned as overrides."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'company_id': self.company.id,
                'field_name': 'account_id',
                'ai_value': str(self.account_ai.id),
                'user_value': str(self.account_user.id),
                'line_description': 'Network equipment',
                'correction_count': 1,
                'auto_apply': False,
            }
        )

        override = AiVendorMemory.get_account_override(
            self.env,
            self.partner,
            'Network equipment purchase',
            company=self.company,
        )
        self.assertIsNone(override)

    def test_different_vendor_no_conflict(self):
        """Corrections for different vendors should be isolated."""
        if not self.account_ai or not self.account_user:
            return
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        other_partner = self.env['res.partner'].create(
            {
                'name': 'Other Vendor Inc',
                'is_company': True,
            }
        )

        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'company_id': self.company.id,
                'field_name': 'account_id',
                'ai_value': str(self.account_ai.id),
                'user_value': str(self.account_user.id),
                'line_description': 'Software license renewal',
                'correction_count': 5,
                'auto_apply': True,
            }
        )

        # Other vendor should not get the override
        override = AiVendorMemory.get_account_override(
            self.env,
            other_partner,
            'Software license renewal',
            company=self.company,
        )
        self.assertIsNone(override)

    def test_get_account_override_empty_description(self):
        """Empty description should return None."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        override = AiVendorMemory.get_account_override(
            self.env,
            self.partner,
            '',
            company=self.company,
        )
        self.assertIsNone(override)

    def test_get_account_override_no_partner(self):
        """None partner should return None."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        override = AiVendorMemory.get_account_override(
            self.env,
            None,
            'Some description',
            company=self.company,
        )
        self.assertIsNone(override)
