from odoo.tests.common import TransactionCase, tagged


# Minimal valid Factur-X CII XML (EN16931 profile)
SAMPLE_FACTURX_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>FX-2024-001</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20240115</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Factur-X Vendor SAS</ram:Name>
        <ram:SpecifiedTaxRegistration>
          <ram:ID>FR98765432101</ram:ID>
        </ram:SpecifiedTaxRegistration>
        <ram:PostalTradeAddress>
          <ram:LineOne>10 avenue des Champs</ram:LineOne>
          <ram:PostcodeCode>75008</ram:PostcodeCode>
          <ram:CityName>Paris</ram:CityName>
          <ram:CountryID>FR</ram:CountryID>
        </ram:PostalTradeAddress>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty>
        <ram:Name>Buyer Company</ram:Name>
      </ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">20240215</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>200.00</ram:CalculatedAmount>
        <ram:BasisAmount>1000.00</ram:BasisAmount>
        <ram:RateApplicablePercent>20.00</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxBasisTotalAmount>1000.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount>200.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>1200.00</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument>
        <ram:LineID>1</ram:LineID>
      </ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct>
        <ram:Name>Consulting services</ram:Name>
        <ram:SellerAssignedID>CONS-001</ram:SellerAssignedID>
      </ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeDelivery>
        <ram:BilledQuantity>10</ram:BilledQuantity>
      </ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice>
          <ram:ChargeAmount>100.00</ram:ChargeAmount>
        </ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax>
          <ram:RateApplicablePercent>20.00</ram:RateApplicablePercent>
        </ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>1000.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""

CREDIT_NOTE_XML = SAMPLE_FACTURX_XML.replace(
    '<ram:TypeCode>380</ram:TypeCode>',
    '<ram:TypeCode>381</ram:TypeCode>',
)


@tagged('post_install', '-at_install')
class TestFacturxParser(TransactionCase):
    """Test Factur-X / ZUGFeRD CII XML parsing."""

    def _parse(self, xml_str):
        from odoo.addons.account_invoice_digitize_ai.models.ai_facturx_parser import parse_facturx_xml

        return parse_facturx_xml(xml_str)

    def test_parse_minimal_invoice(self):
        """Parse a minimal valid Factur-X XML."""
        data = self._parse(SAMPLE_FACTURX_XML)
        self.assertEqual(data['document_type'], 'invoice')
        self.assertFalse(data['is_marked_paid'])

    def test_vendor_extraction(self):
        """Vendor name, VAT, and address should be extracted."""
        data = self._parse(SAMPLE_FACTURX_XML)
        vendor = data['vendor']
        self.assertEqual(vendor['name'], 'Factur-X Vendor SAS')
        self.assertEqual(vendor['vat'], 'FR98765432101')
        self.assertIn('Paris', vendor['address'])
        self.assertEqual(vendor['confidence'], 1.0)

    def test_buyer_extraction(self):
        """Buyer name should be extracted."""
        data = self._parse(SAMPLE_FACTURX_XML)
        self.assertEqual(data['buyer']['name'], 'Buyer Company')

    def test_invoice_fields(self):
        """Invoice reference, dates, and currency should be extracted."""
        data = self._parse(SAMPLE_FACTURX_XML)
        inv = data['invoice']
        self.assertEqual(inv['reference'], 'FX-2024-001')
        self.assertEqual(inv['invoice_date'], '2024-01-15')
        self.assertEqual(inv['due_date'], '2024-02-15')
        self.assertEqual(inv['currency'], 'EUR')
        self.assertFalse(inv['is_credit_note'])

    def test_totals(self):
        """Totals should be extracted correctly."""
        data = self._parse(SAMPLE_FACTURX_XML)
        totals = data['totals']
        self.assertEqual(totals['untaxed_amount'], 1000.0)
        self.assertEqual(totals['tax_amount'], 200.0)
        self.assertEqual(totals['total_amount'], 1200.0)
        self.assertEqual(totals['confidence'], 1.0)

    def test_tax_lines(self):
        """Tax lines should be extracted with rate and amounts."""
        data = self._parse(SAMPLE_FACTURX_XML)
        self.assertEqual(len(data['tax_lines']), 1)
        tax = data['tax_lines'][0]
        self.assertEqual(tax['tax_rate'], 20.0)
        self.assertEqual(tax['base_amount'], 1000.0)
        self.assertEqual(tax['tax_amount'], 200.0)

    def test_line_items(self):
        """Line items should be extracted with description, qty, price."""
        data = self._parse(SAMPLE_FACTURX_XML)
        self.assertEqual(len(data['lines']), 1)
        line = data['lines'][0]
        self.assertEqual(line['description'], 'Consulting services')
        self.assertEqual(line['product_code'], 'CONS-001')
        self.assertEqual(line['quantity'], 10.0)
        self.assertEqual(line['unit_price'], 100.0)
        self.assertEqual(line['subtotal_untaxed'], 1000.0)
        self.assertEqual(line['tax_rate'], 20.0)

    def test_credit_note_detection(self):
        """TypeCode 381 should be detected as credit note."""
        data = self._parse(CREDIT_NOTE_XML)
        self.assertEqual(data['document_type'], 'credit_note')
        self.assertTrue(data['invoice']['is_credit_note'])

    def test_invalid_xml_raises(self):
        """Invalid XML should raise ValueError."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_facturx_parser import parse_facturx_xml

        with self.assertRaises(ValueError):
            parse_facturx_xml('<invalid><xml>')

    def test_empty_xml_raises(self):
        """Empty XML should raise ValueError."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_facturx_parser import parse_facturx_xml

        with self.assertRaises(ValueError):
            parse_facturx_xml('')

    def test_none_xml_raises(self):
        """None XML should raise ValueError."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_facturx_parser import parse_facturx_xml

        with self.assertRaises(ValueError):
            parse_facturx_xml(None)
