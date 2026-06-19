"""Field mapping tests: header fields, currency, partner overrides, vendor pre-identification.

Covers the ai_field_mapper.py methods that were not previously tested:
  - _ai_map_invoice_fields (static): ref, dates, narration, payment_reference
  - _ai_map_currency: currency code → res.currency
  - _ai_apply_partner_overrides: vendor memory partner_id override
  - _ai_pre_identify_vendor: VAT-based vendor identification from text
  - _ai_match_purchase_order: PO matching with tier/confidence
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMapInvoiceFields(TransactionCase):
    """Test AccountMove._ai_map_invoice_fields (static)."""

    def _call(self, inv):
        vals = {}
        confidence = {}
        # Static method — call on the model class
        self.env['account.move']._ai_map_invoice_fields(inv, vals, confidence)
        return vals, confidence

    def test_all_fields_mapped(self):
        """All known invoice fields should be mapped with correct keys."""
        inv = {
            'reference': 'INV-001',
            'invoice_date': '2025-01-15',
            'due_date': '2025-02-15',
            'narration': 'Some notes',
            'payment_reference': 'PAY-001',
            'confidence': 0.9,
        }
        vals, confidence = self._call(inv)
        self.assertEqual(vals['ref'], 'INV-001')
        self.assertEqual(vals['invoice_date'], '2025-01-15')
        self.assertEqual(vals['invoice_date_due'], '2025-02-15')
        self.assertEqual(vals['narration'], 'Some notes')
        self.assertEqual(vals['payment_reference'], 'PAY-001')

    def test_confidence_tracked_for_key_fields(self):
        """Confidence should be tracked for ref, invoice_date, due_date but not narration."""
        inv = {
            'reference': 'X',
            'invoice_date': '2025-01-01',
            'due_date': '2025-02-01',
            'narration': 'Notes',
            'payment_reference': 'PR',
            'confidence': 0.85,
        }
        vals, confidence = self._call(inv)
        self.assertEqual(confidence['ref'], 0.85)
        self.assertEqual(confidence['invoice_date'], 0.85)
        self.assertEqual(confidence['invoice_date_due'], 0.85)
        self.assertNotIn('narration', confidence)
        self.assertNotIn('payment_reference', confidence)

    def test_empty_values_skipped(self):
        """Falsy values should not be mapped."""
        inv = {'reference': '', 'invoice_date': None, 'due_date': False, 'confidence': 0.9}
        vals, confidence = self._call(inv)
        self.assertEqual(len(vals), 0)
        self.assertEqual(len(confidence), 0)

    def test_partial_fields(self):
        """Only provided fields should be mapped."""
        inv = {'reference': 'INV-099', 'confidence': 0.7}
        vals, confidence = self._call(inv)
        self.assertEqual(vals['ref'], 'INV-099')
        self.assertNotIn('invoice_date', vals)
        self.assertEqual(confidence['ref'], 0.7)

    def test_missing_confidence_defaults_zero(self):
        """Missing confidence key should default to 0.0."""
        inv = {'reference': 'X'}
        vals, confidence = self._call(inv)
        self.assertEqual(vals['ref'], 'X')
        self.assertEqual(confidence['ref'], 0.0)


@tagged('post_install', '-at_install')
class TestMapCurrency(TransactionCase):
    """Test AccountMove._ai_map_currency."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def _make_move(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )

    def test_valid_currency_mapped(self):
        """A valid currency code should set currency_id in vals."""
        move = self._make_move()
        original_currency = move.currency_id
        # Find a different active currency
        other = self.env['res.currency'].search(
            [('active', '=', True), ('id', '!=', original_currency.id)],
            limit=1,
        )
        if not other:
            return
        inv = {'currency': other.name}
        vals = {}
        move._ai_map_currency(inv, vals)
        self.assertEqual(vals['currency_id'], other.id)

    def test_same_currency_not_mapped(self):
        """Same currency as current should not produce a val."""
        move = self._make_move()
        inv = {'currency': move.currency_id.name}
        vals = {}
        move._ai_map_currency(inv, vals)
        self.assertNotIn('currency_id', vals)

    def test_unknown_currency_skipped(self):
        """Unknown currency code should not produce a val."""
        move = self._make_move()
        inv = {'currency': 'ZZZZZ'}
        vals = {}
        move._ai_map_currency(inv, vals)
        self.assertNotIn('currency_id', vals)

    def test_empty_currency_skipped(self):
        """Empty/missing currency code should not produce a val."""
        move = self._make_move()
        for inv in ({}, {'currency': ''}, {'currency': None}):
            vals = {}
            move._ai_map_currency(inv, vals)
            self.assertNotIn('currency_id', vals)

    def test_case_insensitive_match(self):
        """Currency matching should be case-insensitive."""
        move = self._make_move()
        original_currency = move.currency_id
        other = self.env['res.currency'].search(
            [('active', '=', True), ('id', '!=', original_currency.id)],
            limit=1,
        )
        if not other:
            return
        inv = {'currency': other.name.lower()}
        vals = {}
        move._ai_map_currency(inv, vals)
        self.assertEqual(vals.get('currency_id'), other.id)


@tagged('post_install', '-at_install')
class TestApplyPartnerOverrides(TransactionCase):
    """Test AccountMove._ai_apply_partner_overrides."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner_a = cls.env['res.partner'].create({'name': 'Override Vendor A', 'is_company': True})
        cls.partner_b = cls.env['res.partner'].create({'name': 'Override Vendor B', 'is_company': True})

    def _make_move(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )

    def test_override_replaces_partner(self):
        """When memory returns a partner_id override, vals should be updated."""
        move = self._make_move()
        vals = {'partner_id': self.partner_a.id}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.AiVendorMemory') as MockMemory:
            MockMemory.get_auto_apply_overrides.return_value = {'partner_id': str(self.partner_b.id)}
            result = move._ai_apply_partner_overrides(self.partner_a, vals, self.company)
        self.assertEqual(result, self.partner_b)
        self.assertEqual(vals['partner_id'], self.partner_b.id)

    def test_no_override_keeps_partner(self):
        """When memory returns no override, partner should remain unchanged."""
        move = self._make_move()
        vals = {'partner_id': self.partner_a.id}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.AiVendorMemory') as MockMemory:
            MockMemory.get_auto_apply_overrides.return_value = {}
            result = move._ai_apply_partner_overrides(self.partner_a, vals, self.company)
        self.assertEqual(result, self.partner_a)
        self.assertEqual(vals['partner_id'], self.partner_a.id)

    def test_invalid_override_ignored(self):
        """An invalid partner_id override should be silently ignored."""
        move = self._make_move()
        vals = {'partner_id': self.partner_a.id}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.AiVendorMemory') as MockMemory:
            MockMemory.get_auto_apply_overrides.return_value = {'partner_id': 'not_a_number'}
            result = move._ai_apply_partner_overrides(self.partner_a, vals, self.company)
        self.assertEqual(result, self.partner_a)


@tagged('post_install', '-at_install')
class TestPreIdentifyVendor(TransactionCase):
    """Test AccountMove._ai_pre_identify_vendor."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'VAT Test Vendor',
                'is_company': True,
                'vat': 'BE0477472701',
            }
        )

    def _make_move(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )

    def test_finds_partner_by_vat(self):
        """Should find partner when VAT appears in extracted text."""
        move = self._make_move()
        text = 'Invoice from ACME Corp\nTVA: BE0477472701\nTotal: 1000.00'
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_document') as MockDoc:
            MockDoc.find_vat_numbers.return_value = ['BE0477472701']
            result = move._ai_pre_identify_vendor(text)
        self.assertEqual(result, self.partner)

    def test_returns_none_when_no_vat(self):
        """Should return None when no VAT is found in text."""
        move = self._make_move()
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_document') as MockDoc:
            MockDoc.find_vat_numbers.return_value = []
            result = move._ai_pre_identify_vendor('No VAT here')
        self.assertIsNone(result)

    def test_returns_none_when_vat_not_matched(self):
        """Should return None when VAT is found but no partner matches."""
        move = self._make_move()
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_document') as MockDoc:
            MockDoc.find_vat_numbers.return_value = ['FR12345678901']
            result = move._ai_pre_identify_vendor('TVA: FR12345678901')
        self.assertIsNone(result)

    def test_first_match_wins(self):
        """When multiple VATs are found, first matching partner wins."""
        move = self._make_move()
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_document') as MockDoc:
            MockDoc.find_vat_numbers.return_value = ['XX000000000', 'BE0477472701']
            result = move._ai_pre_identify_vendor('text')
        self.assertEqual(result, self.partner)


@tagged('post_install', '-at_install')
class TestMatchPurchaseOrder(TransactionCase):
    """Test AccountMove._ai_match_purchase_order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'PO Test Vendor', 'is_company': True})

    def _make_move(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )

    def test_matched_po_returns_confidence(self):
        """A matched PO should return confidence info with tier score."""
        move = self._make_move()
        fake_po = type('FakePO', (), {'name': 'PO-001', 'amount_total': 500.0})()
        data = {'totals': {'total_amount': 500.0}, 'invoice': {'invoice_date': '2025-01-01'}}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_matcher') as MockMatcher:
            MockMatcher.match_purchase_order.return_value = (fake_po, 'exact')
            result, po = move._ai_match_purchase_order('PO-001', self.partner, self.company, data)
        self.assertTrue(result['matched'])
        self.assertEqual(result['po_name'], 'PO-001')
        self.assertEqual(result['confidence'], 0.95)
        self.assertEqual(result['match_tier'], 'exact')
        self.assertEqual(po, fake_po)

    def test_fuzzy_match_lower_confidence(self):
        """A fuzzy PO match should have lower confidence than exact."""
        move = self._make_move()
        fake_po = type('FakePO', (), {'name': 'PO-002', 'amount_total': 300.0})()
        data = {'totals': {}, 'invoice': {}}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_matcher') as MockMatcher:
            MockMatcher.match_purchase_order.return_value = (fake_po, 'fuzzy')
            result, _po = move._ai_match_purchase_order('PO-02', self.partner, self.company, data)
        self.assertEqual(result['confidence'], 0.7)

    def test_no_match_with_ref_returns_warning(self):
        """Unmatched PO ref should return a not-matched warning."""
        move = self._make_move()
        data = {'totals': {}, 'invoice': {}}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_matcher') as MockMatcher:
            MockMatcher.match_purchase_order.return_value = (None, None)
            result, po = move._ai_match_purchase_order('PO-999', self.partner, self.company, data)
        self.assertFalse(result['matched'])
        self.assertIn('PO-999', result.get('message', ''))
        self.assertIsNone(po)

    def test_no_match_no_ref_returns_none(self):
        """No PO ref and no match should return None."""
        move = self._make_move()
        data = {'totals': {}, 'invoice': {}}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_matcher') as MockMatcher:
            MockMatcher.match_purchase_order.return_value = (None, None)
            result, po = move._ai_match_purchase_order(None, self.partner, self.company, data)
        self.assertIsNone(result)
        self.assertIsNone(po)

    def test_matched_po_returned_directly(self):
        """A matched PO should be returned directly (no global cache)."""
        move = self._make_move()
        fake_po = type('FakePO', (), {'name': 'PO-003', 'amount_total': 100.0})()
        data = {'totals': {}, 'invoice': {}}
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_field_mapper.ai_matcher') as MockMatcher:
            MockMatcher.match_purchase_order.return_value = (fake_po, 'exact')
            _result, po = move._ai_match_purchase_order('PO-003', self.partner, self.company, data)
        self.assertEqual(po, fake_po)
