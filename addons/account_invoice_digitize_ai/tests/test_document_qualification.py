from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDocumentQualification(TransactionCase):
    """Test document qualification heuristics."""

    def _qualify(self, text):
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_document_qualifier,
        )

        return ai_document_qualifier.qualify_document(text)

    def test_invoice_keyword_detected(self):
        """Text with 'Facture' → is_likely_invoice = True."""
        result = self._qualify('Facture N° 2024-001\nTotal TTC: 1 200,00 €')
        self.assertTrue(result['is_likely_invoice'])
        self.assertFalse(result['is_proforma'])
        self.assertFalse(result['is_paid'])

    def test_english_invoice_keyword(self):
        """Text with 'Invoice' → is_likely_invoice = True."""
        result = self._qualify('Invoice #INV-2024-042\nTotal: $500.00')
        self.assertTrue(result['is_likely_invoice'])

    def test_proforma_detected(self):
        """Text with 'Devis' → is_proforma = True."""
        result = self._qualify('Devis N° D-2024-015\nTotal TTC: 3 500,00 €')
        self.assertTrue(result['is_proforma'])
        self.assertFalse(result['is_likely_invoice'])

    def test_proforma_wins_over_invoice(self):
        """Text with both invoice and proforma keywords → proforma wins."""
        result = self._qualify('Facture Pro forma\nDevis N° 123\nTotal: 100,00 €')
        self.assertTrue(result['is_proforma'])
        self.assertFalse(result['is_likely_invoice'])

    def test_paid_stamp_detected(self):
        """Text with 'PAYÉ' → is_paid = True."""
        result = self._qualify('Facture N° 2024-001\nPAYÉ\nTotal: 500,00 €')
        self.assertTrue(result['is_paid'])
        self.assertTrue(result['is_likely_invoice'])

    def test_paid_stamp_english(self):
        """Text with 'PAID' → is_paid = True."""
        result = self._qualify('Invoice #42\nPAID\nTotal: $200.00')
        self.assertTrue(result['is_paid'])

    def test_total_labels_only(self):
        """Text with total labels but no 'Facture' → is_likely_invoice."""
        result = self._qualify('Société XYZ\nTotal HT: 1 000,00\nTVA: 200,00\nTotal TTC: 1 200,00')
        self.assertTrue(result['is_likely_invoice'])

    def test_empty_text(self):
        """Empty text → is_likely_invoice = False."""
        result = self._qualify('')
        self.assertFalse(result['is_likely_invoice'])
        self.assertFalse(result['is_proforma'])
        self.assertFalse(result['is_paid'])

    def test_none_text(self):
        """None text → is_likely_invoice = False."""
        result = self._qualify(None)
        self.assertFalse(result['is_likely_invoice'])

    def test_garbage_text(self):
        """Random text without any keywords → is_likely_invoice = False."""
        result = self._qualify('Lorem ipsum dolor sit amet consectetur adipiscing elit')
        self.assertFalse(result['is_likely_invoice'])
        self.assertFalse(result['is_proforma'])
        self.assertFalse(result['is_paid'])

    def test_case_insensitive(self):
        """Keywords should be matched case-insensitively."""
        result = self._qualify('facture n° 123\ntotal ttc: 100,00 €')
        self.assertTrue(result['is_likely_invoice'])

    def test_german_invoice(self):
        """German invoice with 'Rechnung' → is_likely_invoice."""
        result = self._qualify('Rechnung Nr. 2024-100\nNettobetrag: 500,00 €')
        self.assertTrue(result['is_likely_invoice'])
