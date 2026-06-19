import json

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVendorMemory(TransactionCase):
    """Test the AI vendor memory (per-vendor learning from corrections)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Test Vendor SAS',
                'is_company': True,
                'vat': 'FR98765432100',
            }
        )
        # Default threshold = 3
        cls.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_auto_apply_threshold', '3')

    # ------------------------------------------------------------------
    # record_correction
    # ------------------------------------------------------------------

    def test_record_correction_creates_entry(self):
        """First correction for a field should create a new memory entry."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'INV-001', 'FAC-001')

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'ref'),
            ]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.ai_value, 'INV-001')
        self.assertEqual(entry.user_value, 'FAC-001')
        self.assertEqual(entry.correction_count, 1)
        self.assertFalse(entry.auto_apply)

    def test_record_correction_increments_count(self):
        """Repeated identical correction should increment the counter."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        for _ in range(2):
            AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'INV-001', 'FAC-001')

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'ref'),
            ]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.correction_count, 2)
        self.assertFalse(entry.auto_apply)

    def test_record_correction_auto_apply_threshold(self):
        """After reaching threshold, auto_apply should be set to True."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        for _ in range(3):
            AiVendorMemory.record_correction(self.env, self.partner, 'partner_id', '10', '20')

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'partner_id'),
            ]
        )
        self.assertEqual(entry.correction_count, 3)
        self.assertTrue(entry.auto_apply)

    def test_record_correction_different_ai_values(self):
        """Different AI values for the same field create separate entries."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'INV-001', 'FAC-001')
        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'INV-002', 'FAC-002')

        entries = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'ref'),
            ]
        )
        self.assertEqual(len(entries), 2)

    def test_record_correction_custom_threshold(self):
        """Auto-apply threshold should be configurable via settings."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_auto_apply_threshold', '1')

        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        AiVendorMemory.record_correction(self.env, self.partner, 'invoice_date', '2024-01-01', '2024-01-15')

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'invoice_date'),
            ]
        )
        self.assertTrue(entry.auto_apply)

    # ------------------------------------------------------------------
    # get_vendor_context
    # ------------------------------------------------------------------

    def test_get_vendor_context_empty(self):
        """No memory entries should return an empty string."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        ctx = AiVendorMemory.get_vendor_context(self.env, self.partner)
        self.assertEqual(ctx, '')

    def test_get_vendor_context_with_entries(self):
        """Memory entries with count >= 2 should appear in context."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        # Create an entry with count >= 2
        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'field_name': 'partner_id',
                'ai_value': '10',
                'user_value': '20',
                'correction_count': 3,
                'auto_apply': True,
            }
        )

        ctx = AiVendorMemory.get_vendor_context(self.env, self.partner)
        self.assertIn('partner_id', ctx)
        self.assertIn('[AUTO-APPLY]', ctx)
        self.assertIn('3 times', ctx)

    def test_get_vendor_context_ignores_single_corrections(self):
        """Entries with count < 2 should not appear in context."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'field_name': 'ref',
                'ai_value': 'X',
                'user_value': 'Y',
                'correction_count': 1,
            }
        )

        ctx = AiVendorMemory.get_vendor_context(self.env, self.partner)
        self.assertEqual(ctx, '')

    # ------------------------------------------------------------------
    # get_auto_apply_overrides
    # ------------------------------------------------------------------

    def test_get_auto_apply_overrides_empty(self):
        """No auto-apply entries should return empty dict."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        overrides = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner)
        self.assertEqual(overrides, {})

    def test_get_auto_apply_overrides_returns_entries(self):
        """Auto-apply entries should be returned as a dict."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'field_name': 'partner_id',
                'ai_value': '10',
                'user_value': '20',
                'correction_count': 5,
                'auto_apply': True,
            }
        )

        overrides = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner)
        self.assertEqual(overrides.get('partner_id'), '20')

    def test_get_auto_apply_overrides_ignores_non_auto(self):
        """Non-auto-apply entries should not be included."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import AiVendorMemory

        self.env['ai.vendor.memory'].create(
            {
                'partner_id': self.partner.id,
                'field_name': 'ref',
                'ai_value': 'X',
                'user_value': 'Y',
                'correction_count': 1,
                'auto_apply': False,
            }
        )

        overrides = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner)
        self.assertNotIn('ref', overrides)


@tagged('post_install', '-at_install')
class TestCorrectionDetection(TransactionCase):
    """Test correction detection via account.move write() override."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Correction Test Vendor',
                'is_company': True,
            }
        )
        cls.partner2 = cls.env['res.partner'].create(
            {
                'name': 'Other Vendor',
                'is_company': True,
            }
        )

    def test_write_detects_partner_correction(self):
        """Changing partner_id after AI extraction should record a correction."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'ai_extracted_values': json.dumps(
                    {
                        'partner_id': str(self.partner.id),
                    }
                ),
            }
        )

        # Simulate user correcting the partner
        move.write({'partner_id': self.partner2.id})

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
                ('field_name', '=', 'partner_id'),
            ]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.user_value, str(self.partner2.id))

    def test_write_ignores_non_tracked_fields(self):
        """Changing non-tracked fields should not record corrections."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'ai_extracted_values': json.dumps(
                    {
                        'partner_id': str(self.partner.id),
                    }
                ),
            }
        )

        # Change narration (not a tracked field)
        move.write({'narration': 'Some note'})

        entries = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
            ]
        )
        self.assertEqual(len(entries), 0)

    def test_write_ignores_without_snapshot(self):
        """Without ai_extracted_values, no corrections should be detected."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
            }
        )

        move.write({'ref': 'NEW-REF'})

        entries = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
            ]
        )
        self.assertEqual(len(entries), 0)

    def test_write_skips_when_snapshot_being_set(self):
        """When ai_extracted_values is in vals, skip correction detection."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
            }
        )

        # This simulates the initial extraction write
        move.write(
            {
                'ref': 'FAC-001',
                'ai_extracted_values': json.dumps({'ref': 'FAC-001'}),
            }
        )

        entries = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner.id),
            ]
        )
        self.assertEqual(len(entries), 0)
