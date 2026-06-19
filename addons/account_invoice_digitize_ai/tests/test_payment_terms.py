from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentTermsMatching(TransactionCase):
    """Test payment terms matching logic (ai_matcher.match_payment_term)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'Test Vendor', 'is_company': True})
        # Use distinctive names that won't clash with pre-existing Odoo terms
        cls.term_30 = cls.env['account.payment.term'].create({'name': '30 jours net', 'company_id': cls.company.id})
        cls.term_60 = cls.env['account.payment.term'].create({'name': '60 days net', 'company_id': cls.company.id})

    def _match(self, text, partner=None):
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_payment_term

        return match_payment_term(self.env, text, self.company, partner=partner)

    def test_exact_name_match(self):
        """Exact substring match on payment term name."""
        result = self._match('30 jours net')
        self.assertEqual(result, self.term_30)

    def test_partial_name_match(self):
        """Partial substring match — text contained in term name."""
        result = self._match('30 jours')
        self.assertEqual(result, self.term_30)

    def test_day_count_heuristic(self):
        """Day-count regex should match '60 days' to a term containing '60'."""
        result = self._match('Payment due in 60 days from invoice date')
        # In Odoo 19 pre-existing terms may also match — just verify we get
        # a term that has '60' in its name (our term or a pre-existing one).
        self.assertIsNotNone(result)
        self.assertIn('60', result.name)

    def test_no_match(self):
        """Unrecognizable text should return None."""
        # Use a very specific string unlikely to match any pre-existing term
        result = self._match('xyzzy_no_such_payment_term_ever')
        self.assertIsNone(result)

    def test_empty_text(self):
        """Empty text should return None."""
        result = self._match('')
        self.assertIsNone(result)

    def test_none_text(self):
        """None text should return None."""
        result = self._match(None)
        self.assertIsNone(result)

    def test_vendor_history(self):
        """Vendor history should take precedence over fuzzy match."""
        # Create a posted invoice with term_60 for our partner.
        # In Odoo 19, action_post requires at least one invoice line.
        # Odoo 19 removed company_id from account.account
        if 'company_id' in self.env['account.account']._fields:
            account_domain = [('company_id', '=', self.company.id), ('account_type', '=', 'expense')]
        else:
            account_domain = [('account_type', '=', 'expense')]
        account = self.env['account.account'].search(account_domain, limit=1)
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'invoice_payment_term_id': self.term_60.id,
                'invoice_date': '2024-01-01',
                'company_id': self.company.id,
                'invoice_line_ids': [
                    (
                        0,
                        0,
                        {
                            'name': 'Test line',
                            'quantity': 1,
                            'price_unit': 100.0,
                            'account_id': account.id,
                        },
                    )
                ],
            }
        )
        move.action_post()
        # Even though text says "30 jours", vendor history should win
        result = self._match('30 jours', partner=self.partner)
        self.assertEqual(result, self.term_60)
