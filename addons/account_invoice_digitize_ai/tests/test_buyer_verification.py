from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBuyerVerification(TransactionCase):
    """Test buyer verification against the active Odoo company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write(
            {
                'name': 'ACME Corporation',
                'vat': 'FR12345678901',
            }
        )

    def _create_bill(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )

    def test_buyer_vat_matches_company(self):
        """Buyer VAT matches company → no warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'ACME Corp',
                'vat': 'FR12345678901',
            }
        )
        self.assertFalse(result.get('found'))

    def test_buyer_vat_matches_with_spaces(self):
        """Buyer VAT with spaces still matches."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'ACME Corp',
                'vat': 'FR 123 456 789 01',
            }
        )
        self.assertFalse(result.get('found'))

    def test_buyer_vat_mismatch(self):
        """Buyer VAT does not match → warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'Other Company',
                'vat': 'FR99999999999',
            }
        )
        self.assertTrue(result.get('found'))
        self.assertIn('FR99999999999', result['message'])

    def test_buyer_name_matches_company(self):
        """Buyer name matches company name → no warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'ACME Corporation',
            }
        )
        self.assertFalse(result.get('found'))

    def test_buyer_name_substring_match(self):
        """Buyer name is a substring of company name → no warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'ACME',
            }
        )
        self.assertFalse(result.get('found'))

    def test_buyer_name_superstring_match(self):
        """Company name is a substring of buyer name → no warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'ACME Corporation SAS',
            }
        )
        self.assertFalse(result.get('found'))

    def test_buyer_name_mismatch(self):
        """Buyer name does not match → warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'Totally Different Inc.',
            }
        )
        self.assertTrue(result.get('found'))
        self.assertIn('Totally Different', result['message'])

    def test_no_buyer_data(self):
        """Empty buyer data → no warning."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer({})
        self.assertFalse(result.get('found'))

    def test_buyer_name_case_insensitive(self):
        """Name matching is case-insensitive."""
        bill = self._create_bill()
        result = bill._ai_verify_buyer(
            {
                'name': 'acme corporation',
            }
        )
        self.assertFalse(result.get('found'))

    def test_no_company_vat_falls_back_to_name(self):
        """Company without VAT → falls back to name comparison."""
        self.company.write({'vat': False})
        bill = self._create_bill()
        # Buyer has VAT but company doesn't → no match on VAT, warning
        result = bill._ai_verify_buyer(
            {
                'name': 'Other Corp',
                'vat': 'DE123456789',
            }
        )
        self.assertTrue(result.get('found'))
