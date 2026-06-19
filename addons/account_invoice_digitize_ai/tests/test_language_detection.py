from odoo.tests.common import TransactionCase, tagged

from ..models.ai_document import detect_language


@tagged('post_install', '-at_install')
class TestLanguageDetection(TransactionCase):
    """Tests for document language detection (pure Python, no DB)."""

    def test_detect_french(self):
        text = (
            'FACTURE N° 2024-001\n'
            'Montant HT: 1 000,00 EUR\n'
            'TVA 20%: 200,00 EUR\n'
            'Total TTC: 1 200,00 EUR\n'
            'Échéance: 30 jours\n'
            'Remise commerciale\n'
            'Règlement par virement'
        )
        lang, name = detect_language(text)
        self.assertEqual(lang, 'fr')
        self.assertEqual(name, 'French')

    def test_detect_german(self):
        text = (
            'RECHNUNG Nr. 2024-001\n'
            'Betrag netto: 1.000,00 EUR\n'
            'MwSt 19%: 190,00 EUR\n'
            'Gesamt: 1.190,00 EUR\n'
            'Zahlung innerhalb von 30 Tagen\n'
            'Rabatt gewährt\n'
            'Netto Betrag'
        )
        lang, name = detect_language(text)
        self.assertEqual(lang, 'de')
        self.assertEqual(name, 'German')

    def test_detect_english(self):
        text = (
            'INVOICE #2024-001\n'
            'Subtotal: $1,000.00\n'
            'Discount: $50.00\n'
            'Tax: $190.00\n'
            'Total Amount Due: $1,140.00\n'
            'Payment terms: Net 30\n'
            'Balance due upon receipt\n'
            'Quantity ordered'
        )
        lang, name = detect_language(text)
        self.assertEqual(lang, 'en')
        self.assertEqual(name, 'English')

    def test_detect_spanish(self):
        text = (
            'FACTURA N° 2024-001\n'
            'Importe neto: 1.000,00 EUR\n'
            'IVA 21%: 210,00 EUR\n'
            'Cantidad total: 1.210,00 EUR\n'
            'Vencimiento: 30 días\n'
            'Descuento aplicado\n'
            'Pago por transferencia'
        )
        lang, name = detect_language(text)
        self.assertEqual(lang, 'es')
        self.assertEqual(name, 'Spanish')

    def test_detect_italian(self):
        text = (
            'FATTURA N° 2024-001\n'
            'Importo netto: 1.000,00 EUR\n'
            'IVA 22%: 220,00 EUR\n'
            'Totale: 1.220,00 EUR\n'
            'Scadenza: 30 giorni\n'
            'Sconto applicato\n'
            'Pagamento tramite bonifico'
        )
        lang, name = detect_language(text)
        self.assertEqual(lang, 'it')
        self.assertEqual(name, 'Italian')

    def test_ambiguous_returns_none(self):
        """Mixed-language text should return None."""
        text = (
            'INVOICE / FACTURE\nAmount / Montant: 100.00\nTotal / Total: 120.00\nPayment / Règlement\nDiscount / Remise'
        )
        lang, name = detect_language(text)
        self.assertIsNone(lang)
        self.assertIsNone(name)

    def test_short_text_returns_none(self):
        """Text with too few keyword hits should return None."""
        text = 'Total: 100.00 EUR'
        lang, name = detect_language(text)
        self.assertIsNone(lang)

    def test_empty_text(self):
        lang, name = detect_language('')
        self.assertIsNone(lang)
        self.assertIsNone(name)

    def test_none_text(self):
        lang, name = detect_language(None)
        self.assertIsNone(lang)
        self.assertIsNone(name)

    def test_case_insensitive(self):
        """Keywords should match regardless of case."""
        text = (
            'FACTURE COMMERCIALE\n'
            'MONTANT HT: 500,00\n'
            'TVA: 100,00\n'
            'TOTAL TTC: 600,00\n'
            'ÉCHÉANCE: 30 JOURS\n'
            'REMISE: 0,00\n'
            'RÈGLEMENT PAR VIREMENT'
        )
        lang, name = detect_language(text)
        self.assertEqual(lang, 'fr')
