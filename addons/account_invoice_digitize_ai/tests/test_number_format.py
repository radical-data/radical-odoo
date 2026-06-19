from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestNumberFormatDetection(TransactionCase):
    """Test decimal separator detection from document text."""

    def _detect(self, text):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import detect_number_format

        return detect_number_format(text)

    def test_french_comma_decimal(self):
        """French format (1 234,56) → comma_decimal."""
        text = 'Total HT: 1 234,56 €\nTVA: 246,91 €\nTotal TTC: 1 481,47 €'
        self.assertEqual(self._detect(text), 'comma_decimal')

    def test_german_comma_decimal(self):
        """German format (1.234,56) → comma_decimal."""
        text = 'Nettobetrag: 1.234,56 EUR\nMwSt: 234,57 EUR'
        self.assertEqual(self._detect(text), 'comma_decimal')

    def test_english_dot_decimal(self):
        """English format (1,234.56) → dot_decimal."""
        text = 'Subtotal: 1,234.56 USD\nTax: 246.91 USD\nTotal: 1,481.47 USD'
        self.assertEqual(self._detect(text), 'dot_decimal')

    def test_swiss_dot_decimal(self):
        """Swiss format with apostrophe (1'234.56) → dot_decimal."""
        text = "Total: 1'234.56 CHF\nTVA: 95.36 CHF"
        self.assertEqual(self._detect(text), 'dot_decimal')

    def test_simple_comma_decimal(self):
        """Simple amounts without thousands separator."""
        text = 'Montant: 234,56 €\nTVA: 46,91 €'
        self.assertEqual(self._detect(text), 'comma_decimal')

    def test_simple_dot_decimal(self):
        """Simple amounts without thousands separator (dot)."""
        text = 'Amount: 234.56 USD\nTax: 46.91 USD'
        self.assertEqual(self._detect(text), 'dot_decimal')

    def test_no_amounts_returns_none(self):
        """Text without monetary amounts → None."""
        text = 'This is a regular document with no numbers at all.'
        self.assertIsNone(self._detect(text))

    def test_single_amount_returns_none(self):
        """Only one amount found → None (insufficient data)."""
        text = 'Total: 1.234,56 EUR'
        self.assertIsNone(self._detect(text))

    def test_ambiguous_tie_returns_none(self):
        """Equal count of both formats → None."""
        text = 'Amount: 1,234.56\nMontant: 1.234,56'
        self.assertIsNone(self._detect(text))

    def test_empty_string(self):
        """Empty text → None."""
        self.assertIsNone(self._detect(''))

    def test_multiple_comma_amounts(self):
        """Multiple comma-decimal amounts with currency symbols."""
        text = 'Ligne 1: 150,00 €\nLigne 2: 2.500,00 €\nLigne 3: 75,50 €\nTotal: 2.725,50 €'
        self.assertEqual(self._detect(text), 'comma_decimal')
