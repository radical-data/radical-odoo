"""Edge-case tests: currency rounding, large invoices, malformed responses, credit notes.

Covers gaps identified during the project audit:
  - JPY / BHD currency-aware rounding in cross-validation
  - 100+ line invoice handling (no crash, no truncation)
  - Malformed / partial AI responses (graceful degradation)
  - Vendor credit note (in_refund) line building
"""

import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


# ===================================================================
# JPY / BHD currency-aware rounding
# ===================================================================


@tagged('post_install', '-at_install')
class TestCurrencyAwareRounding(TransactionCase):
    """Cross-validation tolerance should adapt to currency decimals."""

    def _validate(self, data):
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        return cross_validate(data)

    def test_jpy_zero_decimal_tolerance(self):
        """JPY (0 decimals) should tolerate 1.0 difference in totals."""
        data = {
            'invoice': {'currency': 'JPY'},
            'totals': {
                'untaxed_amount': 10000,
                'tax_amount': 1000,
                'total_amount': 11001,  # 1 yen off — within tolerance
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)
        self.assertEqual(data['totals']['confidence'], 0.95)

    def test_jpy_over_tolerance_penalized(self):
        """JPY with > 1.0 difference should be penalized."""
        data = {
            'invoice': {'currency': 'JPY'},
            'totals': {
                'untaxed_amount': 10000,
                'tax_amount': 1000,
                'total_amount': 11002,  # 2 yen off — over tolerance
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)
        self.assertLessEqual(data['totals']['confidence'], 0.5)

    def test_bhd_three_decimal_tolerance(self):
        """BHD (3 decimals) should tolerate 0.005 difference."""
        data = {
            'invoice': {'currency': 'BHD'},
            'totals': {
                'untaxed_amount': 100.000,
                'tax_amount': 5.000,
                'total_amount': 105.004,  # 0.004 off — within 0.005
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)
        self.assertEqual(data['totals']['confidence'], 0.95)

    def test_bhd_over_tolerance_penalized(self):
        """BHD with > 0.005 difference should be penalized."""
        data = {
            'invoice': {'currency': 'BHD'},
            'totals': {
                'untaxed_amount': 100.000,
                'tax_amount': 5.000,
                'total_amount': 105.010,  # 0.010 off — over tolerance
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)
        self.assertLessEqual(data['totals']['confidence'], 0.5)

    def test_eur_default_tolerance(self):
        """EUR (2 decimals) should use 0.05 tolerance."""
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 100.00,
                'tax_amount': 20.00,
                'total_amount': 120.04,  # 0.04 off — within 0.05
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_eur_over_tolerance(self):
        """EUR with > 0.05 difference should be penalized."""
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 100.00,
                'tax_amount': 20.00,
                'total_amount': 120.10,  # 0.10 off — over tolerance
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)

    def test_missing_currency_uses_default(self):
        """No currency should use default (2 decimal) tolerance."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 100.00,
                'tax_amount': 20.00,
                'total_amount': 120.04,
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_jpy_line_arithmetic(self):
        """Line arithmetic should use JPY tolerance (1.0) for yen invoices."""
        data = {
            'invoice': {'currency': 'JPY'},
            'totals': {
                'untaxed_amount': 5000,
                'tax_amount': 500,
                'total_amount': 5500,
                'confidence': 0.9,
            },
            'lines': [
                {
                    'description': 'Item A',
                    'quantity': 3,
                    'unit_price': 1667,
                    'subtotal_untaxed': 5000,  # 3×1667=5001, off by 1 — OK for JPY
                    'confidence': 0.9,
                },
            ],
        }
        failures = self._validate(data)
        # Line sum check: 5000 == 5000 — OK
        # Line arithmetic: 3×1667=5001 vs 5000 — within JPY tolerance (1.0)
        self.assertEqual(failures, 0)


# ===================================================================
# 100+ line invoice
# ===================================================================


@tagged('post_install', '-at_install')
class TestLargeInvoice(TransactionCase):
    """Test handling of invoices with 100+ lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.move = cls.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': cls.company.id,
        })

    def test_cross_validate_100_lines(self):
        """Cross-validation should handle 100+ lines without crash."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        lines = []
        for i in range(120):
            lines.append({
                'description': 'Item %d' % i,
                'quantity': 1,
                'unit_price': 10.0,
                'subtotal_untaxed': 10.0,
                'tax_rate': 20.0,
                'confidence': 0.9,
            })
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 1200.0,
                'tax_amount': 240.0,
                'total_amount': 1440.0,
                'confidence': 0.95,
            },
            'lines': lines,
        }
        failures = cross_validate(data)
        self.assertEqual(failures, 0)

    def test_build_100_lines(self):
        """_ai_build_line_vals should handle 100+ lines without crash."""
        lines = []
        for i in range(100):
            line_data = {
                'description': 'Product %03d' % i,
                'quantity': 1,
                'unit_price': 10.0,
                'suggested_account_category': 'merchandise',
            }
            vals = self.move._ai_build_line_vals(line_data, self.company, None)
            if vals:
                lines.append(vals)
        self.assertEqual(len(lines), 100)

    def test_coerce_amounts_100_lines(self):
        """_coerce_amounts should handle 100+ lines with string amounts."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import _coerce_amounts

        lines = []
        for i in range(150):
            lines.append({
                'description': 'Item %d' % i,
                'quantity': '1',
                'unit_price': '10.50',
                'subtotal_untaxed': '10.50',
                'confidence': 0.9,
            })
        data = {'totals': {'untaxed_amount': '1575.00'}, 'lines': lines}
        _coerce_amounts(data)
        self.assertEqual(data['totals']['untaxed_amount'], 1575.0)
        self.assertEqual(data['lines'][0]['quantity'], 1.0)
        self.assertEqual(data['lines'][99]['unit_price'], 10.5)


# ===================================================================
# Malformed AI responses
# ===================================================================


@tagged('post_install', '-at_install')
class TestMalformedResponses(TransactionCase):
    """Test graceful handling of incomplete or malformed AI responses."""

    def _validate(self, data):
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        return cross_validate(data)

    def test_missing_totals_key(self):
        """Missing totals key should not crash."""
        data = {'invoice': {}}
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_null_amounts(self):
        """Null amounts in totals should not crash."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': None,
                'tax_amount': None,
                'total_amount': None,
                'confidence': 0.5,
            },
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_string_amounts_coerced(self):
        """String amounts should be coerced to float before validation."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': '100.00',
                'tax_amount': '20.00',
                'total_amount': '120.00',
                'confidence': 0.95,
            },
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)
        self.assertIsInstance(data['totals']['untaxed_amount'], float)

    def test_non_numeric_string_becomes_none(self):
        """Non-numeric string amounts should become None (no crash)."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 'not_a_number',
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
        }
        self._validate(data)
        self.assertIsNone(data['totals']['untaxed_amount'])

    def test_empty_lines_list(self):
        """Empty lines list should not crash cross-validation."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [],
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_line_missing_all_amounts(self):
        """Line with no numeric fields should not crash (may fail line sum check)."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [
                {'description': 'Mystery item', 'confidence': 0.5},
            ],
        }
        # Should not raise — line sum mismatch (0 vs 100) is expected
        self._validate(data)

    def test_string_line_amounts_coerced(self):
        """String amounts in lines should be coerced to float."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 50.0,
                'tax_amount': 10.0,
                'total_amount': 60.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Item',
                    'quantity': '5',
                    'unit_price': '10.00',
                    'subtotal_untaxed': '50.00',
                    'tax_rate': '20',
                    'confidence': 0.9,
                },
            ],
        }
        self._validate(data)
        self.assertEqual(data['lines'][0]['quantity'], 5.0)
        self.assertEqual(data['lines'][0]['unit_price'], 10.0)
        self.assertEqual(data['lines'][0]['tax_rate'], 20.0)

    def test_build_line_missing_description(self):
        """Line with empty description should be skipped (not crash)."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.env.company.id,
        })
        result = move._ai_build_line_vals({'quantity': 1, 'unit_price': 10.0}, self.env.company, None)
        self.assertIsNone(result)

    def test_build_line_empty_string_description(self):
        """Line with empty string description should be skipped."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.env.company.id,
        })
        result = move._ai_build_line_vals(
            {'description': '', 'quantity': 1, 'unit_price': 10.0},
            self.env.company,
            None,
        )
        self.assertIsNone(result)

    def test_negative_tax_rate_penalized(self):
        """Negative tax rate should be flagged by cross-validation."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Item',
                    'quantity': 1,
                    'unit_price': 100.0,
                    'subtotal_untaxed': 100.0,
                    'tax_rate': -5.0,
                    'confidence': 0.9,
                },
            ],
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)
        self.assertLessEqual(data['lines'][0]['confidence'], 0.3)

    def test_over_100_tax_rate_penalized(self):
        """Tax rate > 100% should be flagged."""
        data = {
            'invoice': {},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Item',
                    'quantity': 1,
                    'unit_price': 100.0,
                    'subtotal_untaxed': 100.0,
                    'tax_rate': 150.0,
                    'confidence': 0.9,
                },
            ],
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)
        self.assertLessEqual(data['lines'][0]['confidence'], 0.3)


# ===================================================================
# Vendor credit note (in_refund)
# ===================================================================


@tagged('post_install', '-at_install')
class TestVendorCreditNote(TransactionCase):
    """Test credit note handling in the extraction pipeline."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Credit Note Vendor',
            'is_company': True,
            'vat': 'FR55555555555',
        })
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_api_key', 'test-key',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_extract_lines', 'True',
        )

    def _make_credit_note_response(self):
        """Build a mock API response for a credit note."""
        return {
            'id': 'msg_cn_test',
            'type': 'message',
            'role': 'assistant',
            'model': 'claude-haiku-4-5-20251001',
            'usage': {'input_tokens': 1000, 'output_tokens': 400},
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps({
                        'document_type': 'credit_note',
                        'is_marked_paid': False,
                        'vendor': {
                            'name': 'Credit Note Vendor',
                            'vat': 'FR55555555555',
                            'confidence': 0.95,
                        },
                        'buyer': {'name': None, 'confidence': 0.0},
                        'invoice': {
                            'reference': 'AV-2025-001',
                            'invoice_date': '2025-01-20',
                            'due_date': '2025-02-20',
                            'currency': 'EUR',
                            'is_credit_note': True,
                            'is_reverse_charge': False,
                            'original_invoice_ref': 'FAC-2024-050',
                            'confidence': 0.9,
                        },
                        'totals': {
                            'untaxed_amount': 500.0,
                            'tax_amount': 100.0,
                            'total_amount': 600.0,
                            'confidence': 0.95,
                        },
                        'tax_lines': [
                            {
                                'tax_rate': 20.0,
                                'base_amount': 500.0,
                                'tax_amount': 100.0,
                                'confidence': 0.9,
                            },
                        ],
                        'table_analysis': {
                            'pricing_mode': 'ht_to_ttc',
                            'line_count': 1,
                        },
                        'lines': [
                            {
                                'description': 'Returned merchandise',
                                'quantity': 5,
                                'unit_price': 100.0,
                                'subtotal_untaxed': 500.0,
                                'tax_rate': 20.0,
                                'is_shipping_line': False,
                                'suggested_account_category': 'merchandise',
                                'confidence': 0.9,
                            },
                        ],
                    }),
                },
            ],
        }

    def test_credit_note_out_invoice_to_out_refund(self):
        """_ai_detect_credit_note should convert out_invoice to out_refund."""
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'company_id': self.company.id,
        })
        vals = {}
        data = {'document_type': 'credit_note'}
        inv = {'is_credit_note': True}
        move._ai_detect_credit_note(data, inv, vals)
        self.assertEqual(vals['move_type'], 'out_refund')

    def test_credit_note_detection(self):
        """_ai_detect_credit_note should convert in_invoice to in_refund."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.company.id,
        })
        vals = {}
        data = {'document_type': 'credit_note'}
        inv = {'is_credit_note': True}
        move._ai_detect_credit_note(data, inv, vals)
        self.assertEqual(vals['move_type'], 'in_refund')

    def test_credit_note_no_change_for_refund(self):
        """If already in_refund, no change needed."""
        move = self.env['account.move'].create({
            'move_type': 'in_refund',
            'company_id': self.company.id,
        })
        vals = {}
        data = {'document_type': 'credit_note'}
        inv = {'is_credit_note': True}
        move._ai_detect_credit_note(data, inv, vals)
        self.assertNotIn('move_type', vals)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_e2e_credit_note_pipeline(self, mock_post):
        """Full pipeline: credit note should set move_type to in_refund and create lines."""
        import base64

        mock_resp = self._make_credit_note_response()
        mock_post.return_value = type('Response', (), {
            'status_code': 200,
            'json': lambda self: mock_resp,
        })()

        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.company.id,
        })
        self.env['ir.attachment'].create({
            'name': 'credit_note.pdf',
            'datas': base64.b64encode(b'%PDF-1.4 fake CN'),
            'mimetype': 'application/pdf',
            'res_model': 'account.move',
            'res_id': move.id,
        })

        api_key = self.env['ir.config_parameter'].sudo().get_param(
            'account_invoice_digitize_ai.ai_api_key',
        )
        attachment = move._ai_get_invoice_attachment()
        move._ai_trigger_extraction(api_key, attachment)

        self.assertEqual(move.move_type, 'in_refund')
        self.assertEqual(move.ai_extraction_status, 'done')
        self.assertEqual(move.ref, 'AV-2025-001')

    def test_cross_validate_credit_note_amounts(self):
        """Cross-validation should work for credit note amounts."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 500.0,
                'tax_amount': 100.0,
                'total_amount': 600.0,
                'confidence': 0.95,
            },
        }
        failures = cross_validate(data)
        self.assertEqual(failures, 0)

    def test_credit_note_line_building(self):
        """Line building should work for credit notes (in_refund)."""
        move = self.env['account.move'].create({
            'move_type': 'in_refund',
            'company_id': self.company.id,
        })
        line_data = {
            'description': 'Returned item',
            'quantity': 2,
            'unit_price': 50.0,
            'suggested_account_category': 'merchandise',
        }
        vals = move._ai_build_line_vals(line_data, self.company, None)
        self.assertIsNotNone(vals)
        self.assertEqual(vals['name'], 'Returned item')
        self.assertEqual(vals['quantity'], 2)
        self.assertEqual(vals['price_unit'], 50.0)


# ===================================================================
# Validator penalties (line arithmetic + line sums)
# ===================================================================


@tagged('post_install', '-at_install')
class TestValidatorPenalties(TransactionCase):
    """Test that cross-validation correctly penalizes detected issues."""

    def _validate(self, data):
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        return cross_validate(data)

    def test_line_arithmetic_mismatch_penalized(self):
        """Line with qty * price != subtotal should be penalized."""
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Widget',
                    'quantity': 5,
                    'unit_price': 10.0,
                    'subtotal_untaxed': 100.0,  # 5*10=50 != 100
                    'confidence': 0.9,
                },
            ],
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)
        self.assertLessEqual(data['lines'][0]['confidence'], 0.5)

    def test_line_arithmetic_within_tolerance_ok(self):
        """Line with small rounding difference should pass."""
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Widget',
                    'quantity': 3,
                    'unit_price': 33.34,
                    'subtotal_untaxed': 100.0,  # 3*33.34=100.02, diff=0.02 < 0.10
                    'confidence': 0.9,
                },
            ],
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)

    def test_line_sum_mismatch_penalized(self):
        """Sum of line subtotals != untaxed_amount should penalize line confidence."""
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 200.0,
                'tax_amount': 40.0,
                'total_amount': 240.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Item A',
                    'quantity': 1,
                    'unit_price': 50.0,
                    'subtotal_untaxed': 50.0,
                    'confidence': 0.9,
                },
                {
                    'description': 'Item B',
                    'quantity': 1,
                    'unit_price': 50.0,
                    'subtotal_untaxed': 50.0,
                    'confidence': 0.9,
                },
            ],  # sum = 100 != 200
        }
        failures = self._validate(data)
        self.assertGreater(failures, 0)
        self.assertLessEqual(data['lines'][0]['confidence'], 0.6)
        self.assertLessEqual(data['lines'][1]['confidence'], 0.6)

    def test_line_sum_within_tolerance_ok(self):
        """Sum of line subtotals within 0.10 tolerance should pass."""
        data = {
            'invoice': {'currency': 'EUR'},
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'lines': [
                {
                    'description': 'Item A',
                    'subtotal_untaxed': 60.05,
                    'confidence': 0.9,
                },
                {
                    'description': 'Item B',
                    'subtotal_untaxed': 40.0,
                    'confidence': 0.9,
                },
            ],  # sum = 100.05, diff = 0.05 < 0.10
        }
        failures = self._validate(data)
        self.assertEqual(failures, 0)


# ===================================================================
# Reverse charge warning
# ===================================================================


@tagged('post_install', '-at_install')
class TestReverseChargeWarning(TransactionCase):
    """Test reverse charge warning generation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def test_reverse_charge_warning_generated(self):
        """is_reverse_charge=True should generate a warning."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.company.id,
        })
        data = {
            'document_type': 'invoice',
            'invoice': {
                'is_reverse_charge': True,
                'reverse_charge_text': 'Art. 196 Directive 2006/112/CE',
            },
        }
        warnings = move._ai_check_warnings(data, None, {}, self.company, confidence={}, mode='guided')
        self.assertIn('reverse_charge_warning', warnings)
        self.assertTrue(warnings['reverse_charge_warning']['found'])
        self.assertIn('Art. 196', warnings['reverse_charge_warning']['message'])

    def test_reverse_charge_warning_without_text(self):
        """Reverse charge without text should still generate a warning."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.company.id,
        })
        data = {
            'document_type': 'invoice',
            'invoice': {'is_reverse_charge': True},
        }
        warnings = move._ai_check_warnings(data, None, {}, self.company, confidence={}, mode='free')
        self.assertIn('reverse_charge_warning', warnings)
        self.assertTrue(warnings['reverse_charge_warning']['found'])
        self.assertIn('reverse charge', warnings['reverse_charge_warning']['message'])

    def test_no_reverse_charge_no_warning(self):
        """Normal invoice should not generate reverse charge warning."""
        move = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'company_id': self.company.id,
        })
        data = {
            'document_type': 'invoice',
            'invoice': {'is_reverse_charge': False},
        }
        warnings = move._ai_check_warnings(data, None, {}, self.company, confidence={}, mode='free')
        self.assertNotIn('reverse_charge_warning', warnings)
