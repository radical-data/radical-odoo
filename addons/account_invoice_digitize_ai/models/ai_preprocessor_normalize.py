"""Shared normalization builders for document pre-processor results.

Both Azure DI and AWS Textract produce extraction results in their own
formats.  This module provides builder functions to assemble a consistent
output schema matching ``parse_facturx_xml()`` in ``ai_facturx_parser.py``.

Adding a new field to the extraction schema only requires changing the
relevant builder here — all pre-processors benefit automatically.
"""


def build_result(vendor, buyer, invoice, totals, lines):
    """Assemble a normalized extraction result dict."""
    return {
        'document_type': 'invoice',
        'is_marked_paid': False,
        'vendor': vendor,
        'buyer': buyer,
        'invoice': invoice,
        'totals': totals,
        'tax_lines': [],
        'lines': lines,
        'table_analysis': {
            'number_format': 'dot_decimal',
            'complexity': 'simple' if len(lines) < 10 else 'complex',
            'line_count': len(lines),
        },
    }


def build_vendor(name='', vat='', address='', email=None, phone=None,
                 website=None, iban=None, bic=None, bank_name=None,
                 confidence=0.0):
    """Build a normalized vendor dict."""
    return {
        'name': name,
        'vat': vat,
        'address': address,
        'email': email,
        'phone': phone,
        'website': website,
        'iban': iban,
        'bic': bic,
        'bank_name': bank_name,
        'confidence': confidence,
    }


def build_buyer(name='', vat=None, address=None, confidence=0.0):
    """Build a normalized buyer dict."""
    return {
        'name': name,
        'vat': vat,
        'address': address,
        'confidence': confidence,
    }


def build_invoice(reference='', invoice_date=None, invoice_date_raw='',
                  due_date=None, due_date_raw='', currency='',
                  payment_reference=None, payment_terms_text=None,
                  payment_method=None, purchase_order_ref=None,
                  delivery_note_ref=None, narration=None,
                  is_credit_note=False, is_reverse_charge=False,
                  reverse_charge_text=None, original_invoice_ref=None,
                  confidence=0.0):
    """Build a normalized invoice dict."""
    return {
        'reference': reference,
        'invoice_date': invoice_date,
        'invoice_date_raw': invoice_date_raw,
        'due_date': due_date,
        'due_date_raw': due_date_raw,
        'currency': currency,
        'payment_reference': payment_reference,
        'payment_terms_text': payment_terms_text,
        'payment_method': payment_method,
        'purchase_order_ref': purchase_order_ref,
        'delivery_note_ref': delivery_note_ref,
        'narration': narration,
        'is_credit_note': is_credit_note,
        'is_reverse_charge': is_reverse_charge,
        'reverse_charge_text': reverse_charge_text,
        'original_invoice_ref': original_invoice_ref,
        'confidence': confidence,
    }


def build_totals(untaxed_amount=0.0, tax_amount=0.0, total_amount=0.0,
                 confidence=0.0):
    """Build a normalized totals dict."""
    return {
        'untaxed_amount': untaxed_amount,
        'tax_amount': tax_amount,
        'total_amount': total_amount,
        'confidence': confidence,
    }


def build_line(description='', product_code=None, quantity=1.0,
               unit_price=0.0, subtotal_untaxed=0.0, tax_rate=None,
               suggested_account_category=None, discount_percent=None,
               confidence=0.0):
    """Build a normalized line item dict."""
    return {
        'description': description,
        'product_code': product_code,
        'quantity': quantity,
        'unit_price': unit_price,
        'subtotal_untaxed': subtotal_untaxed,
        'tax_rate': tax_rate,
        'suggested_account_category': suggested_account_category,
        'discount_percent': discount_percent,
        'confidence': confidence,
    }
