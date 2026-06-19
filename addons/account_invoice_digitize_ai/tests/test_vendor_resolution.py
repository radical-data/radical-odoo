"""Tests for vendor resolution in preview wizard."""

import json

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVendorResolution(TransactionCase):
    """Test the vendor resolution feature in the preview wizard."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Known vendor (exists in DB)
        cls.known_vendor = cls.env['res.partner'].create(
            {
                'name': 'Known Vendor SARL',
                'vat': 'FR12345678901',
                'is_company': True,
            }
        )

        # Another vendor for selection testing
        cls.other_vendor = cls.env['res.partner'].create(
            {
                'name': 'Other Vendor SAS',
                'vat': 'FR99887766551',
                'is_company': True,
            }
        )

        # Purchase journal
        cls.purchase_journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

        # Extraction data with a KNOWN vendor
        cls.data_known_vendor = {
            'vendor': {'name': 'Known Vendor SARL', 'vat': 'FR12345678901', 'confidence': 0.9},
            'invoice': {'reference': 'INV-001', 'invoice_date': '2026-01-15'},
            'totals': {'total_amount': 1200.00, 'untaxed_amount': 1000.00, 'tax_amount': 200.00, 'confidence': 0.85},
            'lines': [{'description': 'Service', 'quantity': 1, 'unit_price': 1000.00, 'tax_rate': 20.0}],
            'document_type': 'invoice',
        }

        # Extraction data with an UNKNOWN vendor
        cls.data_unknown_vendor = {
            'vendor': {'name': 'Infomaniak Network SA', 'vat': 'CHE103167648', 'confidence': 0.9},
            'invoice': {'reference': 'INV-002', 'invoice_date': '2026-01-20'},
            'totals': {'total_amount': 360.00, 'untaxed_amount': 300.00, 'tax_amount': 60.00, 'confidence': 0.8},
            'lines': [{'description': 'Hosting', 'quantity': 1, 'unit_price': 300.00, 'tax_rate': 20.0}],
            'document_type': 'invoice',
        }

    def _create_invoice(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'journal_id': self.purchase_journal.id,
            }
        )

    def _create_wizard(self, move, data):
        return self.env['ai.preview.wizard'].create(
            {
                'move_id': move.id,
                'preview_data': json.dumps(data),
            }
        )

    # ---------------------------------------------------------------
    # Vendor match detection
    # ---------------------------------------------------------------

    def test_wizard_shows_resolution_when_no_match(self):
        """vendor_match_found should be False when vendor is unknown."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        self.assertFalse(wizard.vendor_match_found)

    def test_wizard_hides_resolution_when_match(self):
        """vendor_match_found should be True when vendor is known."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_known_vendor)
        self.assertTrue(wizard.vendor_match_found)

    def test_extracted_vendor_info(self):
        """Extracted vendor name and VAT should be available on wizard."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        self.assertEqual(wizard.vendor_extracted_name, 'Infomaniak Network SA')
        self.assertEqual(wizard.vendor_extracted_vat, 'CHE103167648')

    # ---------------------------------------------------------------
    # Pre-fill new vendor fields
    # ---------------------------------------------------------------

    def test_new_vendor_name_prefilled(self):
        """new_vendor_name should be pre-filled from extraction data."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        self.assertEqual(wizard.new_vendor_name, 'Infomaniak Network SA')

    def test_new_vendor_vat_prefilled(self):
        """new_vendor_vat should be pre-filled from extraction data."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        self.assertEqual(wizard.new_vendor_vat, 'CHE103167648')

    # ---------------------------------------------------------------
    # Apply with selected partner
    # ---------------------------------------------------------------

    def test_apply_with_selected_partner(self):
        """Selecting an existing vendor should fill partner_id on invoice."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        wizard.selected_partner_id = self.other_vendor.id
        wizard.action_apply()

        self.assertEqual(move.partner_id, self.other_vendor)
        self.assertEqual(move.ai_extraction_status, 'done')

    # ---------------------------------------------------------------
    # Apply with new vendor creation
    # ---------------------------------------------------------------

    def test_apply_with_new_vendor(self):
        """Creating a new vendor should create a partner and fill partner_id."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        wizard.create_new_vendor = True
        wizard.new_vendor_name = 'Infomaniak Network SA'
        wizard.new_vendor_vat = 'CHE103167648'
        wizard.action_apply()

        self.assertTrue(move.partner_id)
        self.assertEqual(move.partner_id.name, 'Infomaniak Network SA')
        self.assertEqual(move.ai_extraction_status, 'done')

    def test_new_vendor_is_company(self):
        """Newly created vendor should be marked as company."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        wizard.create_new_vendor = True
        wizard.new_vendor_name = 'New Corp'
        wizard.action_apply()

        self.assertTrue(move.partner_id.is_company)

    def test_new_vendor_has_supplier_rank(self):
        """Newly created vendor should have supplier_rank = 1."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        wizard.create_new_vendor = True
        wizard.new_vendor_name = 'New Corp'
        wizard.action_apply()

        self.assertEqual(move.partner_id.supplier_rank, 1)

    # ---------------------------------------------------------------
    # Apply without selection (current behavior)
    # ---------------------------------------------------------------

    def test_apply_without_selection(self):
        """Not selecting any vendor should leave partner_id empty."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_unknown_vendor)
        # Don't set selected_partner_id or create_new_vendor
        wizard.action_apply()

        self.assertFalse(move.partner_id)
        self.assertEqual(move.ai_extraction_status, 'done')

    # ---------------------------------------------------------------
    # Force partner takes priority over auto-match
    # ---------------------------------------------------------------

    def test_force_partner_overrides_auto_match(self):
        """_force_partner_id should override the auto-matched vendor."""
        move = self._create_invoice()
        # Data has known vendor, but user selects a different one
        wizard = self._create_wizard(move, self.data_known_vendor)
        wizard.selected_partner_id = self.other_vendor.id
        wizard.action_apply()

        # User's choice should win over auto-match
        self.assertEqual(move.partner_id, self.other_vendor)

    # ---------------------------------------------------------------
    # Known vendor — no resolution needed
    # ---------------------------------------------------------------

    def test_known_vendor_auto_applied(self):
        """When vendor is matched, extraction should apply normally."""
        move = self._create_invoice()
        wizard = self._create_wizard(move, self.data_known_vendor)
        # Don't set any resolution fields
        wizard.action_apply()

        self.assertEqual(move.partner_id, self.known_vendor)
        self.assertEqual(move.ai_extraction_status, 'done')
