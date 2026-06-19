"""Tests for purchase order matching (optional purchase dependency)."""

import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPONormalization(TransactionCase):
    """Test PO reference normalization (no purchase module needed)."""

    def test_normalize_basic(self):
        from ..models.ai_matcher import _normalize_po_ref

        self.assertEqual(_normalize_po_ref('PO 12345'), '12345')
        self.assertEqual(_normalize_po_ref('P.O. 00789'), '789')
        self.assertEqual(_normalize_po_ref('PO#ABC-001'), 'ABC-001')
        self.assertEqual(_normalize_po_ref('PO-042'), '42')

    def test_normalize_edge_cases(self):
        from ..models.ai_matcher import _normalize_po_ref

        self.assertEqual(_normalize_po_ref(None), '')
        self.assertEqual(_normalize_po_ref(''), '')
        self.assertEqual(_normalize_po_ref('   '), '')
        self.assertEqual(_normalize_po_ref('PO 000'), '0')

    def test_normalize_no_prefix(self):
        from ..models.ai_matcher import _normalize_po_ref

        self.assertEqual(_normalize_po_ref('CMD-2024-001'), 'CMD-2024-001')
        self.assertEqual(_normalize_po_ref('12345'), '12345')


@tagged('post_install', '-at_install')
class TestPOMatcherGraceful(TransactionCase):
    """Test PO matcher gracefully handles missing purchase module."""

    def test_purchase_not_installed(self):
        from ..models.ai_matcher import match_purchase_order

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_matcher._is_purchase_installed',
            return_value=False,
        ):
            po, tier = match_purchase_order(self.env, 'PO001')
            self.assertIsNone(po)
            self.assertIsNone(tier)

    def test_no_ref_no_partner(self):
        from ..models.ai_matcher import match_purchase_order

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_matcher._is_purchase_installed',
            return_value=True,
        ):
            po, tier = match_purchase_order(self.env, None, partner=None)
            self.assertIsNone(po)

    def test_no_match_returns_none(self):
        """PO ref provided but no matching record."""
        from ..models.ai_matcher import match_purchase_order

        if 'purchase.order' not in self.env:
            return

        partner = self.env['res.partner'].create({'name': 'PO Test Vendor', 'is_company': True})
        po, tier = match_purchase_order(
            self.env,
            'NONEXISTENT-PO-999',
            partner=partner,
            company=self.env.company,
        )
        self.assertIsNone(po)
        self.assertIsNone(tier)


@tagged('post_install', '-at_install')
class TestPOMatcherWithPurchase(TransactionCase):
    """Test PO matching when purchase module is installed.

    These tests are skipped if the purchase module is not available.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.purchase_installed = 'purchase.order' in cls.env
        if not cls.purchase_installed:
            return
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'PO Match Test Vendor',
                'is_company': True,
            }
        )
        cls.product = cls.env['product.product'].create(
            {
                'name': 'Test Product PO',
                'default_code': 'TPO-001',
            }
        )
        cls.po = cls.env['purchase.order'].create(
            {
                'partner_id': cls.partner.id,
                'company_id': cls.env.company.id,
                'order_line': [
                    (
                        0,
                        0,
                        {
                            'name': 'Consulting services Q1 2025',
                            'product_qty': 10.0,
                            'price_unit': 100.0,
                            'product_id': cls.product.id,
                        },
                    )
                ],
            }
        )
        cls.po.button_confirm()

    def test_exact_match(self):
        if not self.purchase_installed:
            return
        from ..models.ai_matcher import match_purchase_order

        po, tier = match_purchase_order(
            self.env,
            self.po.name,
            partner=self.partner,
            company=self.env.company,
        )
        self.assertEqual(po, self.po)
        self.assertEqual(tier, 'exact')

    def test_fuzzy_match_with_prefix(self):
        if not self.purchase_installed:
            return
        from ..models.ai_matcher import match_purchase_order

        # Add 'PO ' prefix to the reference
        ref_with_prefix = 'PO ' + self.po.name
        po, tier = match_purchase_order(
            self.env,
            ref_with_prefix,
            partner=self.partner,
            company=self.env.company,
        )
        self.assertIsNotNone(po)
        self.assertIn(tier, ('exact', 'fuzzy'))

    def test_amount_date_match(self):
        if not self.purchase_installed:
            return
        from ..models.ai_matcher import match_purchase_order

        po, tier = match_purchase_order(
            self.env,
            None,
            partner=self.partner,
            company=self.env.company,
            total_amount=self.po.amount_total,
            invoice_date=str(self.po.date_order),
        )
        self.assertEqual(po, self.po)
        self.assertEqual(tier, 'amount_date')

    def test_line_match_by_product(self):
        if not self.purchase_installed:
            return
        from ..models.ai_matcher import match_purchase_order_line

        line_data = {'product_code': 'TPO-001', 'description': 'Consulting'}
        pol = match_purchase_order_line(self.env, self.po, line_data, partner=self.partner)
        self.assertIsNotNone(pol)
        self.assertEqual(pol.product_id, self.product)

    def test_line_match_by_description(self):
        if not self.purchase_installed:
            return
        from ..models.ai_matcher import match_purchase_order_line

        line_data = {'description': 'Consulting services quarterly'}
        pol = match_purchase_order_line(self.env, self.po, line_data)
        self.assertIsNotNone(pol)

    def test_line_match_by_qty_price(self):
        if not self.purchase_installed:
            return
        from ..models.ai_matcher import match_purchase_order_line

        line_data = {'description': 'Some item', 'quantity': 10.0, 'unit_price': 100.0}
        pol = match_purchase_order_line(self.env, self.po, line_data)
        self.assertIsNotNone(pol)


@tagged('post_install', '-at_install')
class TestPOWarningInPipeline(TransactionCase):
    """Test PO warning generation in the extraction pipeline."""

    def test_po_ref_no_match_generates_warning(self):
        """When PO ref is found but purchase module not installed, store info."""
        move = self.env['account.move'].create({'move_type': 'in_invoice'})
        data = {
            'vendor': {'name': 'Test Vendor', 'confidence': 0.9},
            'invoice': {
                'reference': 'INV-001',
                'invoice_date': '2025-01-15',
                'purchase_order_ref': 'PO-2025-999',
                'confidence': 0.8,
            },
            'totals': {
                'untaxed_amount': 1000.0,
                'tax_amount': 200.0,
                'total_amount': 1200.0,
                'confidence': 0.9,
            },
            'document_type': 'invoice',
        }
        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_matcher._is_purchase_installed',
            return_value=False,
        ):
            move._ai_apply_extraction(data)

        conf = json.loads(move.ai_confidence)
        po_info = conf.get('purchase_order', {})
        self.assertTrue(po_info)
        self.assertEqual(po_info.get('ref'), 'PO-2025-999')
        self.assertFalse(po_info.get('matched'))

    def test_preview_shows_po_ref(self):
        """Preview wizard should show PO ref if extracted."""
        move = self.env['account.move'].create({'move_type': 'in_invoice'})
        data = {
            'vendor': {'name': 'Test', 'confidence': 0.9},
            'invoice': {
                'reference': 'INV-001',
                'invoice_date': '2025-01-15',
                'purchase_order_ref': 'CMD-2025-042',
                'confidence': 0.8,
            },
            'totals': {
                'untaxed_amount': 500.0,
                'tax_amount': 100.0,
                'total_amount': 600.0,
                'confidence': 0.9,
            },
        }
        wizard = self.env['ai.preview.wizard'].create(
            {
                'move_id': move.id,
                'preview_data': json.dumps(data),
            }
        )
        self.assertEqual(wizard.purchase_order_ref, 'CMD-2025-042')


@tagged('post_install', '-at_install')
class TestFacturxPOExtraction(TransactionCase):
    """Test PO reference extraction from Factur-X XML."""

    def test_buyer_order_ref_extracted(self):
        from ..models.ai_facturx_parser import parse_facturx_xml

        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>FA-2025-001</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20250115</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Seller Corp</ram:Name>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty>
        <ram:Name>Buyer Corp</ram:Name>
      </ram:BuyerTradeParty>
      <ram:BuyerOrderReferencedDocument>
        <ram:IssuerAssignedID>PO-2025-042</ram:IssuerAssignedID>
      </ram:BuyerOrderReferencedDocument>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxBasisTotalAmount>1000.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount>200.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>1200.00</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""
        result = parse_facturx_xml(xml)
        self.assertEqual(result['invoice']['purchase_order_ref'], 'PO-2025-042')

    def test_no_buyer_order_ref(self):
        from ..models.ai_facturx_parser import parse_facturx_xml

        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>FA-2025-002</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">20250115</udt:DateTimeString>
    </ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Seller Corp</ram:Name>
      </ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxBasisTotalAmount>500.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount>100.00</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>600.00</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>"""
        result = parse_facturx_xml(xml)
        self.assertIsNone(result['invoice']['purchase_order_ref'])
