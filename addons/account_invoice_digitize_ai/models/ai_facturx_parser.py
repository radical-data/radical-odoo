"""Factur-X / ZUGFeRD CII XML parser.

Parses Cross-Industry Invoice (CII) XML into the same dict format
as Claude's extraction response.  Supports all profiles (Minimum,
Basic, EN16931, Extended).  Handles invoices and credit notes.

These are not Odoo models — they are imported by account_move.py.
"""

import logging

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# lxml is a standard Odoo dependency (always available).
# ---------------------------------------------------------------------------
try:
    from lxml import etree as _etree

    _LXML_AVAILABLE = True
except ImportError:
    _etree = None
    _LXML_AVAILABLE = False

# CII namespace map
_CII_NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
    'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100',
}


def parse_facturx_xml(xml_data):
    """Parse a Factur-X / ZUGFeRD CII XML into a dict matching Claude's output schema.

    Handles all profiles (Minimum, Basic, EN16931, Extended) — extracts
    whatever data is available.

    Args:
        xml_data: XML as ``bytes`` or ``str``.

    Returns:
        dict with keys: ``vendor``, ``buyer``, ``invoice``, ``totals``,
        ``tax_lines``, ``lines``, ``document_type``, ``table_analysis``.
        All confidence scores are ``1.0`` (structured data).

    Raises:
        ValueError: If *xml_data* is empty or cannot be parsed.
    """
    if not _LXML_AVAILABLE:
        raise ValueError('lxml is not available')
    if not xml_data:
        raise ValueError('Empty XML data')

    if isinstance(xml_data, str):
        xml_data = xml_data.encode('utf-8')

    try:
        parser = _etree.XMLParser(resolve_entities=False, no_network=True)
        root = _etree.fromstring(xml_data, parser=parser)
    except _etree.XMLSyntaxError as exc:
        raise ValueError('Invalid XML: %s' % exc) from exc

    doc = _find_cii_document(root)
    if doc is None:
        raise ValueError('No CII document found in XML')

    vendor = _parse_facturx_vendor(doc)
    buyer = _parse_facturx_buyer(doc)
    # ExchangedDocument is a sibling of SupplyChainTradeTransaction — search from root
    invoice = _parse_facturx_invoice(root)
    totals = _parse_facturx_totals(doc)
    tax_lines = _parse_facturx_tax_lines(doc)
    lines = _parse_facturx_lines(doc)

    # Document type from TypeCode (in ExchangedDocument, not SupplyChainTradeTransaction)
    type_code = _text(root, './/ram:TypeCode') or ''
    document_type = 'credit_note' if type_code == '381' else 'invoice'

    return {
        'document_type': document_type,
        'is_marked_paid': False,
        'vendor': vendor,
        'buyer': buyer,
        'invoice': invoice,
        'totals': totals,
        'tax_lines': tax_lines,
        'lines': lines,
        'table_analysis': {
            'number_format': 'dot_decimal',
            'complexity': 'simple' if len(lines) < 10 else 'complex',
            'line_count': len(lines),
        },
    }


# -- Helpers ----------------------------------------------------------------


def _text(element, xpath):
    """Extract text from the first node matching *xpath*, or ``None``."""
    node = element.find(xpath, _CII_NS)
    if node is not None and node.text:
        return node.text.strip()
    return None


def _float(element, xpath):
    """Extract a float from the first node matching *xpath*, or ``None``."""
    val = _text(element, xpath)
    if val:
        try:
            return float(val)
        except ValueError:
            return None
    return None


def _find_cii_document(root):
    """Locate the main CII document element in various namespace layouts."""
    # Standard CII root
    doc = root.find('.//rsm:SupplyChainTradeTransaction', _CII_NS)
    if doc is not None:
        return doc
    # Some ZUGFeRD 1.x use different root namespace
    for child in root:
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag == 'SupplyChainTradeTransaction':
            return child
    # Fallback: try root itself
    if root.find('.//ram:ApplicableHeaderTradeSettlement', _CII_NS) is not None:
        return root
    return None


def _parse_facturx_vendor(doc):
    """Extract vendor (seller) information."""
    seller = doc.find('.//ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty', _CII_NS)
    if seller is None:
        return {'name': '', 'vat': '', 'confidence': 1.0}

    name = _text(seller, 'ram:Name') or ''
    vat_node = seller.find('ram:SpecifiedTaxRegistration/ram:ID', _CII_NS)
    vat = vat_node.text.strip() if vat_node is not None and vat_node.text else ''

    # Address
    addr = seller.find('ram:PostalTradeAddress', _CII_NS)
    address_parts = []
    if addr is not None:
        for field in ['ram:LineOne', 'ram:PostcodeCode', 'ram:CityName', 'ram:CountryID']:
            val = _text(addr, field)
            if val:
                address_parts.append(val)

    # Email
    email_node = seller.find('.//ram:EmailURIUniversalCommunication/ram:URIID', _CII_NS)
    email = email_node.text.strip() if email_node is not None and email_node.text else ''

    return {
        'name': name,
        'vat': vat,
        'address': ', '.join(address_parts),
        'email': email,
        'confidence': 1.0,
    }


def _parse_facturx_buyer(doc):
    """Extract buyer information."""
    buyer = doc.find('.//ram:ApplicableHeaderTradeAgreement/ram:BuyerTradeParty', _CII_NS)
    if buyer is None:
        return {'name': '', 'vat': '', 'confidence': 1.0}

    name = _text(buyer, 'ram:Name') or ''
    vat_node = buyer.find('ram:SpecifiedTaxRegistration/ram:ID', _CII_NS)
    vat = vat_node.text.strip() if vat_node is not None and vat_node.text else ''

    addr = buyer.find('ram:PostalTradeAddress', _CII_NS)
    address_parts = []
    if addr is not None:
        for field in ['ram:LineOne', 'ram:PostcodeCode', 'ram:CityName', 'ram:CountryID']:
            val = _text(addr, field)
            if val:
                address_parts.append(val)

    return {
        'name': name,
        'vat': vat,
        'address': ', '.join(address_parts),
        'confidence': 1.0,
    }


def _parse_facturx_invoice(doc):
    """Extract invoice header fields."""
    ref = _text(doc, './/ram:ExchangedDocument/ram:ID')
    if ref is None:
        ref = _text(doc, './/rsm:ExchangedDocument/ram:ID')

    # Dates — CII uses ram:DateTimeString with format="102" (YYYYMMDD)
    invoice_date = _parse_cii_date(doc, './/ram:ExchangedDocument/ram:IssueDateTime')
    if invoice_date is None:
        invoice_date = _parse_cii_date(doc, './/rsm:ExchangedDocument/ram:IssueDateTime')
    due_date = _parse_cii_date(
        doc, './/ram:ApplicableHeaderTradeSettlement/ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime'
    )

    # Currency
    currency_node = doc.find('.//ram:ApplicableHeaderTradeSettlement/ram:InvoiceCurrencyCode', _CII_NS)
    currency = currency_node.text.strip() if currency_node is not None and currency_node.text else ''

    # Payment reference
    pay_ref = _text(doc, './/ram:ApplicableHeaderTradeSettlement/ram:PaymentReference')

    # Purchase order reference (buyer order)
    po_ref = _text(doc, './/ram:ApplicableHeaderTradeAgreement/ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID')

    # Credit note detection
    type_code = _text(doc, './/ram:TypeCode') or ''
    is_credit_note = type_code == '381'

    return {
        'reference': ref or '',
        'invoice_date': invoice_date or '',
        'due_date': due_date or '',
        'currency': currency,
        'payment_reference': pay_ref or '',
        'purchase_order_ref': po_ref,
        'is_credit_note': is_credit_note,
        'confidence': 1.0,
    }


def _parse_cii_date(element, xpath):
    """Parse a CII date element (format 102 = YYYYMMDD) → ISO YYYY-MM-DD."""
    node = element.find(xpath + '/udt:DateTimeString', _CII_NS)
    if node is None:
        node = element.find(xpath + '/ram:DateTimeString', _CII_NS)
    if node is not None and node.text:
        raw = node.text.strip()
        if len(raw) == 8 and raw.isdigit():
            return '%s-%s-%s' % (raw[:4], raw[4:6], raw[6:8])
        return raw
    return None


def _parse_facturx_totals(doc):
    """Extract totals from the settlement monetary summation."""
    summation = doc.find(
        './/ram:ApplicableHeaderTradeSettlement/ram:SpecifiedTradeSettlementHeaderMonetarySummation', _CII_NS
    )
    if summation is None:
        return {'untaxed_amount': 0.0, 'tax_amount': 0.0, 'total_amount': 0.0, 'confidence': 1.0}

    untaxed = _float(summation, 'ram:TaxBasisTotalAmount') or 0.0
    tax = _float(summation, 'ram:TaxTotalAmount') or 0.0
    total = _float(summation, 'ram:GrandTotalAmount') or 0.0
    # DuePayableAmount may also be present
    if not total:
        total = _float(summation, 'ram:DuePayableAmount') or 0.0

    return {
        'untaxed_amount': untaxed,
        'tax_amount': tax,
        'total_amount': total,
        'confidence': 1.0,
    }


def _parse_facturx_tax_lines(doc):
    """Extract per-rate tax breakdown from the settlement."""
    tax_lines = []
    for tax_node in doc.findall('.//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax', _CII_NS):
        rate = _float(tax_node, 'ram:RateApplicablePercent') or 0.0
        base = _float(tax_node, 'ram:BasisAmount') or 0.0
        tax_amount = _float(tax_node, 'ram:CalculatedAmount') or 0.0
        tax_lines.append(
            {
                'tax_label': 'TVA %s%%' % rate if rate else 'TVA',
                'tax_rate': rate,
                'base_amount': base,
                'tax_amount': tax_amount,
                'confidence': 1.0,
            }
        )
    return tax_lines


def _parse_facturx_lines(doc):
    """Extract invoice line items."""
    lines = []
    for item in doc.findall('.//ram:IncludedSupplyChainTradeLineItem', _CII_NS):
        desc = _text(item, './/ram:SpecifiedTradeProduct/ram:Name') or ''
        product_code = _text(item, './/ram:SpecifiedTradeProduct/ram:SellerAssignedID')
        qty = _float(item, './/ram:SpecifiedLineTradeDelivery/ram:BilledQuantity')
        unit_price = _float(
            item,
            './/ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount',
        )
        subtotal = _float(
            item,
            './/ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount',
        )
        tax_rate = _float(
            item,
            './/ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent',
        )

        lines.append(
            {
                'description': desc,
                'product_code': product_code,
                'quantity': qty or 1.0,
                'unit_price': unit_price,
                'subtotal_untaxed': subtotal,
                'tax_rate': tax_rate,
                'confidence': 1.0,
            }
        )
    return lines
