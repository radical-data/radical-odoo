from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestIbanValidation(TransactionCase):
    """Test IBAN checksum validation in cross-validation."""

    def _validate(self, iban):
        """Run cross-validation on a minimal data dict with the given IBAN."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_validator

        data = {
            'vendor': {'iban': iban, 'confidence': 0.9},
            'totals': {},
            'invoice': {},
        }
        ai_validator.cross_validate(data)
        return data['vendor']

    def test_valid_french_iban(self):
        """Valid French IBAN passes checksum."""
        vendor = self._validate('FR76 3000 6000 0112 3456 7890 189')
        self.assertTrue(vendor['iban_valid'])
        self.assertEqual(vendor['confidence'], 0.9)

    def test_valid_german_iban(self):
        """Valid German IBAN passes checksum."""
        vendor = self._validate('DE89 3704 0044 0532 0130 00')
        self.assertTrue(vendor['iban_valid'])

    def test_valid_luxembourg_iban(self):
        """Valid Luxembourg IBAN passes checksum."""
        vendor = self._validate('LU28 0019 4006 4475 0000')
        self.assertTrue(vendor['iban_valid'])

    def test_invalid_checksum(self):
        """IBAN with wrong check digits fails and lowers confidence."""
        vendor = self._validate('FR00 3000 6000 0112 3456 7890 189')
        self.assertFalse(vendor['iban_valid'])
        self.assertEqual(vendor['confidence'], 0.5)

    def test_invalid_format_too_short(self):
        """IBAN too short fails format check."""
        vendor = self._validate('FR76')
        self.assertFalse(vendor['iban_valid'])
        self.assertEqual(vendor['confidence'], 0.5)

    def test_invalid_format_no_country(self):
        """IBAN without country prefix fails format check."""
        vendor = self._validate('1234567890123456')
        self.assertFalse(vendor['iban_valid'])
        self.assertEqual(vendor['confidence'], 0.5)

    def test_missing_iban(self):
        """No IBAN → no validation, no error."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_validator

        data = {
            'vendor': {'confidence': 0.9},
            'totals': {},
            'invoice': {},
        }
        ai_validator.cross_validate(data)
        self.assertNotIn('iban_valid', data['vendor'])
        self.assertEqual(data['vendor']['confidence'], 0.9)

    def test_iban_with_dashes(self):
        """IBAN with dashes is cleaned and validated."""
        vendor = self._validate('FR76-3000-6000-0112-3456-7890-189')
        self.assertTrue(vendor['iban_valid'])

    def test_iban_lowercase(self):
        """Lowercase IBAN is uppercased and validated."""
        vendor = self._validate('fr76 3000 6000 0112 3456 7890 189')
        self.assertTrue(vendor['iban_valid'])
