"""Correction tracking tests: write override, line corrections, account resolution.

Covers the vendor memory learning system:
  - Header correction detection (partner_id, ref, dates)
  - Line account correction detection
  - Line snapshot lifecycle (create, update, detect)
  - Account resolution (vendor memory override vs standard match)
  - Mode guards (corrections only recorded in guided mode)
  - Customer invoice skipping
"""

import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHeaderCorrections(TransactionCase):
    """Test _ai_collect_header_corrections and write() override."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Vendor A', 'is_company': True})
        cls.partner_b = cls.env['res.partner'].create({'name': 'Vendor B', 'is_company': True})
        cls.move = cls.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': cls.company.id,
                'partner_id': cls.partner.id,
            }
        )

    def _set_snapshot(self, snapshot_dict):
        self.move.ai_extracted_values = json.dumps(snapshot_dict)

    def test_collect_corrections_detects_partner_change(self):
        """Changing partner_id should produce a correction entry."""
        self._set_snapshot({'partner_id': str(self.partner.id)})
        corrections = self.move._ai_collect_header_corrections({'partner_id': self.partner_b.id}, 'guided')
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0][2], 'partner_id')  # field_name
        self.assertEqual(corrections[0][3], str(self.partner.id))  # ai_val
        self.assertEqual(corrections[0][4], str(self.partner_b.id))  # user_val

    def test_collect_corrections_ignores_same_value(self):
        """Same value should not produce a correction."""
        self._set_snapshot({'partner_id': str(self.partner.id)})
        corrections = self.move._ai_collect_header_corrections({'partner_id': self.partner.id}, 'guided')
        self.assertEqual(len(corrections), 0)

    def test_collect_corrections_skips_free_mode(self):
        """No corrections collected in free mode."""
        self._set_snapshot({'partner_id': str(self.partner.id)})
        corrections = self.move._ai_collect_header_corrections({'partner_id': self.partner_b.id}, 'free')
        self.assertEqual(len(corrections), 0)

    def test_collect_corrections_skips_simplified_mode(self):
        """No corrections collected in simplified mode."""
        self._set_snapshot({'partner_id': str(self.partner.id)})
        corrections = self.move._ai_collect_header_corrections({'partner_id': self.partner_b.id}, 'simplified')
        self.assertEqual(len(corrections), 0)

    def test_collect_corrections_skips_snapshot_write(self):
        """Writing ai_extracted_values itself should not trigger corrections."""
        self._set_snapshot({'ref': 'INV-001'})
        corrections = self.move._ai_collect_header_corrections(
            {'ref': 'INV-002', 'ai_extracted_values': '{}'}, 'guided'
        )
        self.assertEqual(len(corrections), 0)

    def test_collect_corrections_skips_customer_invoice(self):
        """Customer invoices should not trigger corrections."""
        customer_move = self.env['account.move'].create(
            {
                'move_type': 'out_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )
        customer_move.ai_extracted_values = json.dumps({'ref': 'INV-001'})
        corrections = customer_move._ai_collect_header_corrections({'ref': 'INV-002'}, 'guided')
        self.assertEqual(len(corrections), 0)

    def test_collect_corrections_skips_no_snapshot(self):
        """No corrections if no snapshot exists."""
        self.move.ai_extracted_values = False
        corrections = self.move._ai_collect_header_corrections({'ref': 'INV-002'}, 'guided')
        self.assertEqual(len(corrections), 0)

    def test_collect_corrections_skips_untracked_field(self):
        """Fields not in _AI_TRACKED_FIELDS should not produce corrections."""
        self._set_snapshot({'narration': 'old note'})
        corrections = self.move._ai_collect_header_corrections({'narration': 'new note'}, 'guided')
        self.assertEqual(len(corrections), 0)

    def test_write_calls_record_correction(self):
        """write() should call AiVendorMemory.record_correction for detected changes."""
        self._set_snapshot({'ref': 'INV-001'})
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_extraction_mode', 'guided')
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_line_builder.AiVendorMemory') as MockMemory:
            self.move.write({'ref': 'INV-002'})
            MockMemory.record_correction.assert_called_once()
            args = MockMemory.record_correction.call_args[0]
            self.assertEqual(args[2], 'ref')  # field_name
            self.assertEqual(args[3], 'INV-001')  # ai_val
            self.assertEqual(args[4], 'INV-002')  # user_val


@tagged('post_install', '-at_install')
class TestLineCorrections(TransactionCase):
    """Test _ai_collect_line_snapshots and _ai_detect_line_corrections."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Line Test Vendor', 'is_company': True})

        # Find two distinct expense accounts (Odoo 19: company_ids, older: company_id)
        # Must be expense-type to avoid Odoo 19 constraint on receivable/payable in purchases
        Account = cls.env['account.account']
        if 'company_ids' in Account._fields:
            co_domain = ('company_ids', 'in', cls.company.id)
        else:
            co_domain = ('company_id', '=', cls.company.id)
        domain = [co_domain, ('account_type', 'in', ['expense', 'expense_direct_cost'])]
        accounts = Account.search(domain, limit=2)
        if len(accounts) < 2:
            # Fallback: any non-receivable/payable accounts
            domain = [co_domain, ('account_type', 'not in', ['asset_receivable', 'liability_payable'])]
            accounts = Account.search(domain, limit=2)
        cls.account_a = accounts[0] if accounts else None
        cls.account_b = accounts[1] if len(accounts) > 1 else None

    def test_collect_line_snapshots_guided(self):
        """Line snapshots should be collected in guided mode when lines change."""
        if not self.account_a:
            return
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )
        ai_lines = [{'description': 'Service A', 'account_id': self.account_a.id}]
        move.ai_extracted_values = json.dumps({'_lines': ai_lines})
        snapshots = move._ai_collect_line_snapshots({'invoice_line_ids': [(0, 0, {'name': 'x'})]}, 'guided')
        self.assertIn(move.id, snapshots)

    def test_collect_line_snapshots_free_mode_skips(self):
        """No line snapshots in free mode."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )
        move.ai_extracted_values = json.dumps({'_lines': [{'description': 'X', 'account_id': 1}]})
        snapshots = move._ai_collect_line_snapshots({'invoice_line_ids': [(0, 0, {'name': 'x'})]}, 'free')
        self.assertEqual(len(snapshots), 0)

    def test_collect_line_snapshots_no_line_change_skips(self):
        """No snapshots when invoice_line_ids is not in vals."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )
        move.ai_extracted_values = json.dumps({'_lines': [{'description': 'X', 'account_id': 1}]})
        snapshots = move._ai_collect_line_snapshots({'ref': 'INV-001'}, 'guided')
        self.assertEqual(len(snapshots), 0)

    def test_detect_line_corrections_records_account_change(self):
        """Account changes on lines should be recorded in vendor memory."""
        if not self.account_a or not self.account_b:
            return
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )
        # Create a line with account_b (user changed from AI's account_a)
        move.write(
            {
                'invoice_line_ids': [
                    (
                        0,
                        0,
                        {
                            'name': 'Consulting fees',
                            'quantity': 1,
                            'price_unit': 100,
                            'account_id': self.account_b.id,
                        },
                    )
                ],
            }
        )
        # Simulate AI had assigned account_a
        ai_lines = [{'description': 'Consulting fees', 'account_id': self.account_a.id}]
        line_snapshots = {move.id: (move, ai_lines)}

        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_line_builder.AiVendorMemory') as MockMemory:
            move._ai_detect_line_corrections(line_snapshots)
            MockMemory.record_line_correction.assert_called_once()
            args = MockMemory.record_line_correction.call_args[0]
            self.assertEqual(args[2], 'Consulting fees')  # description
            self.assertEqual(args[3], str(self.account_a.id))  # ai_val
            self.assertEqual(args[4], str(self.account_b.id))  # user_val

    def test_detect_line_corrections_same_account_no_record(self):
        """No correction recorded when account matches AI snapshot."""
        if not self.account_a:
            return
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
                'partner_id': self.partner.id,
            }
        )
        move.write(
            {
                'invoice_line_ids': [
                    (
                        0,
                        0,
                        {
                            'name': 'Same account line',
                            'quantity': 1,
                            'price_unit': 50,
                            'account_id': self.account_a.id,
                        },
                    )
                ],
            }
        )
        ai_lines = [{'description': 'Same account line', 'account_id': self.account_a.id}]
        line_snapshots = {move.id: (move, ai_lines)}

        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_line_builder.AiVendorMemory') as MockMemory:
            move._ai_detect_line_corrections(line_snapshots)
            MockMemory.record_line_correction.assert_not_called()


@tagged('post_install', '-at_install')
class TestLineSnapshot(TransactionCase):
    """Test _ai_update_line_snapshot lifecycle."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def test_update_snapshot_populates_lines(self):
        """_ai_update_line_snapshot should populate _lines with current accounts."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        # Set initial snapshot with empty _lines placeholder
        move.ai_extracted_values = json.dumps({'_lines': []})
        Account = self.env['account.account']
        if 'company_ids' in Account._fields:
            co_domain = ('company_ids', 'in', self.company.id)
        else:
            co_domain = ('company_id', '=', self.company.id)
        account = Account.search([co_domain], limit=1)
        if not account:
            return
        move.write(
            {
                'invoice_line_ids': [
                    (
                        0,
                        0,
                        {
                            'name': 'Test line',
                            'quantity': 1,
                            'price_unit': 100,
                            'account_id': account.id,
                        },
                    )
                ],
            }
        )
        move._ai_update_line_snapshot()
        snapshot = json.loads(move.ai_extracted_values)
        self.assertTrue(len(snapshot['_lines']) >= 1)
        self.assertEqual(snapshot['_lines'][0]['description'], 'Test line')
        self.assertEqual(snapshot['_lines'][0]['account_id'], account.id)

    def test_update_snapshot_no_extracted_values(self):
        """_ai_update_line_snapshot should be no-op when no snapshot exists."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        move.ai_extracted_values = False
        move._ai_update_line_snapshot()  # Should not raise
        self.assertFalse(move.ai_extracted_values)

    def test_update_snapshot_no_lines_key(self):
        """_ai_update_line_snapshot should be no-op when _lines key missing."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        move.ai_extracted_values = json.dumps({'partner_id': '1'})
        move._ai_update_line_snapshot()
        snapshot = json.loads(move.ai_extracted_values)
        self.assertNotIn('_lines', snapshot)


@tagged('post_install', '-at_install')
class TestAccountResolution(TransactionCase):
    """Test _ai_resolve_account: vendor memory override vs standard match."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Resolve Vendor', 'is_company': True})
        Account = cls.env['account.account']
        if 'company_ids' in Account._fields:
            co_domain = ('company_ids', 'in', cls.company.id)
        else:
            co_domain = ('company_id', '=', cls.company.id)
        cls.account = Account.search([co_domain], limit=1)

    def test_resolve_account_memory_override(self):
        """Vendor memory override should take priority."""
        if not self.account:
            return
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        with patch('odoo.addons.account_invoice_digitize_ai.models.ai_line_builder.AiVendorMemory') as MockMemory:
            MockMemory.get_account_override.return_value = self.account.id
            result = move._ai_resolve_account({}, 'Office supplies', self.company, self.partner, None)
            self.assertEqual(result, self.account.id)
            MockMemory.get_account_override.assert_called_once()

    def test_resolve_account_fallback_to_matcher(self):
        """When no memory override, should fall back to ai_matcher."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        with (
            patch('odoo.addons.account_invoice_digitize_ai.models.ai_line_builder.AiVendorMemory') as MockMemory,
            patch('odoo.addons.account_invoice_digitize_ai.models.ai_line_builder.ai_matcher') as MockMatcher,
        ):
            MockMemory.get_account_override.return_value = None
            MockMatcher.match_account.return_value = None
            result = move._ai_resolve_account(
                {'suggested_account_category': 'supplies'},
                'Office supplies',
                self.company,
                self.partner,
                None,
            )
            self.assertIsNone(result)
            MockMatcher.match_account.assert_called_once()
