"""Prompt templates and constants for AI extraction.

This file is not an Odoo model — it contains pure Python constants
used by the extraction pipeline in account_move.py.
"""

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an invoice data extraction assistant. Your task is to extract \
structured data from the provided invoice document and return a JSON object.

RULES:
- Return ONLY valid JSON. No commentary, no markdown, no explanation outside the JSON.
- All amounts must be numbers (float), never strings.
- All dates must be in ISO format (YYYY-MM-DD).
- Fields you cannot find or are not present must be null, never invented.
- Detect the document language automatically — extract data regardless of language.
- For number formats: respect the convention used on the invoice \
  (comma vs dot as decimal separator). Always return amounts as standard floats \
  (dot as decimal separator) in the JSON output. If a pre-detected number format \
  is provided in the prompt context, use it as a strong hint for interpreting amounts.
- For dates: use the vendor's country context to disambiguate DD/MM vs MM/DD. \
  Default to DD/MM/YYYY for European invoices.
- NEVER invent or hallucinate values. If unsure, set confidence to a low value \
  and return null for the field.
- FIRST classify the document: set "document_type" to "invoice", "credit_note", \
  "proforma", or "unknown". If the document is a quote, estimate, or pro-forma \
  invoice, set "proforma". If you cannot determine the type, set "unknown".
- Detect "PAID"/"PAYÉ"/"BEZAHLT"/"PAGADO"/"PAGATO" stamps, watermarks, or \
  handwritten annotations. Set "is_marked_paid" to true if detected.
- If PDF metadata is provided (creator software, author, title, creation date), \
  use it as supplementary context. The creator software may indicate the invoicing \
  system (e.g. SAP, Sage, QuickBooks). The creation date can serve as a fallback \
  for the invoice date if it cannot be extracted from the document body.
- If structured table data (markdown format) is provided alongside the document text, \
  use it as the primary source for line item extraction. It was extracted programmatically \
  from the PDF and preserves the original table structure. Cross-reference it with the \
  raw text for context (vendor info, totals, payment terms). If the structured table \
  data appears incomplete or inconsistent, prefer the raw text.
- If QR code data is provided (Swiss QR-bill or EPC QR), treat it as high-confidence \
  structured data. Use the QR IBAN as the vendor IBAN, verify your total_amount against \
  the QR amount, and use the QR reference as payment_reference. Prefer QR data over \
  document text for IBAN, amount, and reference.
"""

# ---------------------------------------------------------------------------
# Extraction JSON schema (included in user prompt)
# ---------------------------------------------------------------------------

# Private: shared JSON structure (document_type → table_analysis interior)
_SCHEMA_JSON_BODY = """\
  "document_type": "invoice | credit_note | proforma | unknown",
  "is_marked_paid": false,
  "vendor": {
    "name": "",
    "vat": "",
    "address": "",
    "email": null,
    "phone": null,
    "website": null,
    "iban": null,
    "bic": null,
    "bank_name": null,
    "confidence": 0.0
  },
  "buyer": {
    "name": "",
    "vat": null,
    "address": null,
    "confidence": 0.0
  },
  "invoice": {
    "reference": "",
    "invoice_date": "YYYY-MM-DD",
    "invoice_date_raw": "",
    "due_date": null,
    "due_date_raw": null,
    "currency": "",
    "payment_reference": null,
    "payment_terms_text": null,
    "payment_method": null,
    "purchase_order_ref": null,
    "delivery_note_ref": null,
    "narration": null,
    "is_credit_note": false,
    "is_reverse_charge": false,
    "reverse_charge_text": null,
    "original_invoice_ref": null,
    "confidence": 0.0
  },
  "totals": {
    "untaxed_amount": 0.0,
    "tax_amount": 0.0,
    "total_amount": 0.0,
    "confidence": 0.0
  },
  "tax_lines": [
    {
      "tax_label": "",
      "tax_rate": 0.0,
      "base_amount": 0.0,
      "tax_amount": 0.0,
      "confidence": 0.0
    }
  ],
  "table_analysis": {
    "columns_detected": [],
    "pricing_mode": "ht_to_ttc | ttc_only | ht_only",
    "tax_display": "per_line | grouped_at_bottom | single_rate_global",
    "has_discounts": false,
    "has_deposits": false,
    "has_early_payment_discount": false,
    "has_shipping_line": false,
    "line_count": 0,
    "complexity": "simple | complex",
    "number_format": "dot_decimal | comma_decimal",
    "date_format_detected": "",
    "tax_info_per_line": "rate_only | amount_only | rate_and_amount | none",
    "has_tax_summary_table": false"""

# Private: lines array block (appended to JSON body for with-lines variant)
_SCHEMA_LINES_BLOCK = """
  },
  "lines": [
    {
      "description": "",
      "product_code": null,
      "unit_of_measure": null,
      "quantity": null,
      "unit_price": null,
      "unit_price_is_tax_included": false,
      "discount_percent": null,
      "discount_amount": null,
      "subtotal_untaxed": null,
      "subtotal_tax_included": null,
      "tax_rate": null,
      "tax_amount": null,
      "is_shipping_line": false,
      "suggested_account_category": "",
      "confidence": 0.0
    }
  ]"""

# Private: shared field rules (common to both variants)
_FIELD_RULES_COMMON = """\
Field rules:
- "document_type": Classify the document. If proforma/quote, set to "proforma".
- "is_marked_paid": true if the document has a "PAID"/"PAYÉ"/"BEZAHLT" stamp.
- "vendor.iban": Extract IBAN from payment details / footer if present.
- "invoice.invoice_date_raw" / "due_date_raw": Date as printed on the invoice.
- "invoice.payment_method": "bank_transfer", "direct_debit", "check", "card", \
  "cash", or null.
- "invoice.is_reverse_charge": true if intra-community reverse charge / \
  autoliquidation is mentioned (Art. 196, Art. 262 ter-I, § 13b UStG, etc.).
- "invoice.purchase_order_ref": The buyer's purchase order number. Look for labels \
  like "PO Number", "Purchase Order", "Your Order", "Order Ref", "Commande", \
  "N° de commande", "Bestellnummer", "Orden de compra", "Ordine d'acquisto". \
  This is the BUYER's reference, not the vendor's invoice number. Set null if absent.
- "totals": Always fill all three amounts. untaxed + tax = total. \
  For credit notes, all amounts (and line quantities) must be POSITIVE — \
  the accounting system handles the sign reversal.
"""

# Private: field rules specific to the with-lines variant
_FIELD_RULES_WITH_LINES = """\
- "tax_lines": Extract the TVA summary table if present. This is more reliable \
  than per-line tax data.
- Tax category codes: On receipts and restaurant tickets, tax categories may be \
  indicated by numbers like (1), (2), (3) or letters (A), (B), (C) next to each \
  line item, with a legend at the bottom mapping each code to a tax rate. ALWAYS \
  use this legend to assign the correct tax rate per line — never guess from the \
  product name. Example: if line shows "FRUIT 5.50 (1)" and legend says \
  "(1) 10.00%", the tax rate for that line is 10%, not 5.5%.
- "table_analysis": ALWAYS fill this section, even when line extraction is disabled.
- "lines": Only fill when line extraction is requested. Each line must have \
  "subtotal_untaxed" filled (calculate from unit_price × quantity if needed). \
  Set null for fields not present on the invoice, never invent values. \
  When tax category codes are present (see above), use the legend to set the \
  correct "tax_rate" for each line.
- "is_shipping_line": true if the line represents shipping/freight/delivery charges.
- "suggested_account_category": One of the following categories, or "" if unsure: \
  consulting, it_services, office_supplies, shipping, freight, telecom, insurance, \
  rent, maintenance, advertising, travel, training, cleaning, subscriptions, \
  software, legal, accounting, bank_fees, utilities, raw_materials, merchandise, \
  subcontracting.
"""

# Private: field rules specific to the no-lines variant
_FIELD_RULES_NO_LINES = """\
- "tax_lines": Extract the TVA summary table if present.
- Tax category codes: On receipts and restaurant tickets, tax categories may be \
  indicated by numbers like (1), (2), (3) or letters (A), (B), (C) next to each \
  line item, with a legend at the bottom mapping each code to a tax rate. ALWAYS \
  use this legend to assign the correct tax rate in tax_lines.
- "table_analysis": ALWAYS fill this section even though lines are not extracted.
"""

# --- Public constants (composed from private parts) -----------------------

EXTRACTION_SCHEMA = (
    'Return a JSON object with EXACTLY this structure:\n{\n'
    + _SCHEMA_JSON_BODY
    + _SCHEMA_LINES_BLOCK
    + '\n}\n\n'
    + _FIELD_RULES_COMMON
    + _FIELD_RULES_WITH_LINES
)

EXTRACTION_SCHEMA_NO_LINES = (
    'Return a JSON object with EXACTLY this structure (no "lines" array — '
    'line extraction is disabled):\n{\n'
    + _SCHEMA_JSON_BODY
    + '\n  }\n}\n\n'
    + _FIELD_RULES_COMMON
    + _FIELD_RULES_NO_LINES
)

# ---------------------------------------------------------------------------
# Fiscal context template (injected with real data at runtime)
# ---------------------------------------------------------------------------

FISCAL_CONTEXT_TEMPLATE = """\
FISCAL CONTEXT for this Odoo instance:
- Company: {company_name}
- Country: {country_code}
- Currency: {currency}
- Chart of accounts: {chart_name}

IMPORTANT: You may ONLY suggest accounts from the lists below. \
NEVER invent account numbers. If no account matches, return null and set \
low confidence.

{account_section}

Available purchase taxes:
{tax_list}

{vendor_context}"""

FISCAL_CONTEXT_TAX_ONLY_TEMPLATE = """\
FISCAL CONTEXT for this Odoo instance:
- Company: {company_name}
- Country: {country_code}
- Currency: {currency}

Available purchase taxes:
{tax_list}"""

# ---------------------------------------------------------------------------
# Account section templates
# ---------------------------------------------------------------------------

ACCOUNT_SECTION_VENDOR = """\
Accounts previously used for this vendor (highest priority — strongly prefer these):
{vendor_accounts}

Other frequently used expense accounts in this company:
{company_accounts}"""

ACCOUNT_SECTION_NO_VENDOR = """\
Frequently used expense accounts in this company:
{company_accounts}

Full chart of accounts (use only if no match above):
{all_accounts}"""

ACCOUNT_SECTION_NEW_COMPANY = """\
Available expense accounts:
{all_accounts}"""

# ---------------------------------------------------------------------------
# Multilingual total label patterns (for text pre-processing + prompt context)
# ---------------------------------------------------------------------------

UNTAXED_LABELS = {
    'fr': [
        'Total HT',
        'Montant HT',
        'Total Hors Taxes',
        'Montant Hors Taxes',
        'Base HT',
        'Net HT',
    ],
    'en': [
        'Subtotal',
        'Sub-total',
        'Net Amount',
        'Total excl. VAT',
        'Total excluding VAT',
        'Total excluding tax',
        'Amount before tax',
        'Pre-tax total',
        'Taxable amount',
        'Net total',
        'Total before VAT',
        'Total before tax',
        'Amount excl. tax',
        'Gross amount',
    ],
    'de': [
        'Nettobetrag',
        'Gesamtbetrag netto',
        'Summe netto',
        'Betrag ohne MwSt',
        'Zwischensumme',
        'Rechnungsbetrag netto',
    ],
    'es': [
        'Base imponible',
        'Total sin IVA',
        'Importe neto',
        'Subtotal',
        'Total sin impuestos',
        'Importe sin IVA',
    ],
    'it': [
        'Totale imponibile',
        'Imponibile',
        'Totale netto',
        'Importo netto',
        'Totale esclusa IVA',
    ],
    'nl': [
        'Bedrag excl. BTW',
        'Totaal excl. BTW',
        'Nettobedrag',
        'Subtotaal',
    ],
    'pt': [
        'Total sem IVA',
        'Valor sem IVA',
        'Base tributável',
        'Valor líquido',
    ],
}

TAX_LABELS = {
    'fr': ['TVA', 'Montant TVA', 'Total TVA', 'Taxe'],
    'en': ['VAT', 'Tax', 'Tax amount', 'Total VAT', 'Sales tax', 'VAT amount'],
    'de': [
        'MwSt',
        'Mehrwertsteuer',
        'MwSt-Betrag',
        'Umsatzsteuer',
        'USt',
        'Steuerbetrag',
    ],
    'es': ['IVA', 'Importe IVA', 'Total IVA', 'Impuesto'],
    'it': ['IVA', 'Importo IVA', 'Totale IVA', 'Imposta'],
    'nl': ['BTW', 'BTW-bedrag', 'Totaal BTW'],
    'pt': ['IVA', 'Valor do IVA', 'Total IVA'],
}

TOTAL_LABELS = {
    'fr': [
        'Total TTC',
        'Montant TTC',
        'Total Toutes Taxes Comprises',
        'Net à payer',
        'Montant à payer',
        'Total à payer',
        'Solde à payer',
    ],
    'en': [
        'Total',
        'Grand total',
        'Total due',
        'Amount due',
        'Total incl. VAT',
        'Total including VAT',
        'Total including tax',
        'Amount payable',
        'Balance due',
        'Invoice total',
        'Total amount',
    ],
    'de': [
        'Bruttobetrag',
        'Gesamtbetrag brutto',
        'Summe brutto',
        'Rechnungsbetrag',
        'Gesamtbetrag',
        'Endbetrag',
        'Zahlbetrag',
        'Betrag inkl. MwSt',
    ],
    'es': [
        'Total',
        'Total con IVA',
        'Importe total',
        'Total a pagar',
        'Total factura',
        'Importe con IVA',
    ],
    'it': [
        'Totale',
        'Totale fattura',
        'Totale con IVA',
        'Importo totale',
        'Totale da pagare',
        'Totale ivato',
    ],
    'nl': ['Totaal', 'Totaal incl. BTW', 'Totaalbedrag', 'Te betalen'],
    'pt': ['Total', 'Total com IVA', 'Valor total', 'Total a pagar'],
}

# Combined flat list for regex scanning
ALL_TOTAL_PATTERNS = []
for _labels in (UNTAXED_LABELS, TAX_LABELS, TOTAL_LABELS):
    for _lang_labels in _labels.values():
        ALL_TOTAL_PATTERNS.extend(_lang_labels)

# ---------------------------------------------------------------------------
# Invoice keyword patterns (for document type qualification)
# ---------------------------------------------------------------------------

INVOICE_KEYWORDS = [
    'Facture',
    'Invoice',
    'Rechnung',
    'Factura',
    'Fattura',
    'Faktuur',
    'Fatura',
    'Avoir',
    'Credit Note',
    'Gutschrift',
    'Nota di credito',
    'Nota de crédito',
]

PROFORMA_KEYWORDS = [
    'Pro forma',
    'Proforma',
    'Pro-forma',
    'Devis',
    'Quote',
    'Quotation',
    'Estimate',
    'Angebot',
    'Kostenvoranschlag',
    'Presupuesto',
    'Preventivo',
    'Offerte',
]

PAID_KEYWORDS = [
    'PAYÉ',
    'PAID',
    'BEZAHLT',
    'PAGADO',
    'PAGATO',
    'ACQUITTÉ',
    'RÉGLÉ',
    'SETTLED',
]


# ---------------------------------------------------------------------------
# Structured output JSON Schema for Anthropic tool_use
# ---------------------------------------------------------------------------
# This schema is used with the tool_use API to guarantee valid JSON output.
# Claude MUST return data matching this schema — no parsing fallbacks needed.

_VENDOR_SCHEMA = {
    'type': 'object',
    'properties': {
        'name': {'type': ['string', 'null']},
        'vat': {'type': ['string', 'null']},
        'address': {'type': ['string', 'null']},
        'email': {'type': ['string', 'null']},
        'phone': {'type': ['string', 'null']},
        'website': {'type': ['string', 'null']},
        'iban': {'type': ['string', 'null']},
        'bic': {'type': ['string', 'null']},
        'bank_name': {'type': ['string', 'null']},
        'confidence': {'type': 'number'},
    },
    'required': ['name', 'confidence'],
}

_BUYER_SCHEMA = {
    'type': 'object',
    'properties': {
        'name': {'type': ['string', 'null']},
        'vat': {'type': ['string', 'null']},
        'address': {'type': ['string', 'null']},
        'confidence': {'type': 'number'},
    },
    'required': ['name', 'confidence'],
}

_INVOICE_SCHEMA = {
    'type': 'object',
    'properties': {
        'reference': {'type': ['string', 'null']},
        'invoice_date': {'type': ['string', 'null']},
        'invoice_date_raw': {'type': ['string', 'null']},
        'due_date': {'type': ['string', 'null']},
        'due_date_raw': {'type': ['string', 'null']},
        'currency': {'type': ['string', 'null']},
        'payment_reference': {'type': ['string', 'null']},
        'payment_terms_text': {'type': ['string', 'null']},
        'payment_method': {'type': ['string', 'null']},
        'purchase_order_ref': {'type': ['string', 'null']},
        'delivery_note_ref': {'type': ['string', 'null']},
        'narration': {'type': ['string', 'null']},
        'is_credit_note': {'type': 'boolean'},
        'is_reverse_charge': {'type': 'boolean'},
        'reverse_charge_text': {'type': ['string', 'null']},
        'original_invoice_ref': {'type': ['string', 'null']},
        'confidence': {'type': 'number'},
    },
    'required': ['reference', 'invoice_date', 'confidence'],
}

_TOTALS_SCHEMA = {
    'type': 'object',
    'properties': {
        'untaxed_amount': {'type': ['number', 'null']},
        'tax_amount': {'type': ['number', 'null']},
        'total_amount': {'type': ['number', 'null']},
        'confidence': {'type': 'number'},
    },
    'required': ['untaxed_amount', 'tax_amount', 'total_amount', 'confidence'],
}

_TAX_LINE_SCHEMA = {
    'type': 'object',
    'properties': {
        'tax_label': {'type': ['string', 'null']},
        'tax_rate': {'type': ['number', 'null']},
        'base_amount': {'type': ['number', 'null']},
        'tax_amount': {'type': ['number', 'null']},
        'confidence': {'type': 'number'},
    },
    'required': ['tax_rate', 'confidence'],
}

_TABLE_ANALYSIS_SCHEMA = {
    'type': 'object',
    'properties': {
        'columns_detected': {'type': 'array', 'items': {'type': 'string'}},
        'pricing_mode': {'type': 'string', 'enum': ['ht_to_ttc', 'ttc_only', 'ht_only']},
        'tax_display': {'type': 'string', 'enum': ['per_line', 'grouped_at_bottom', 'single_rate_global']},
        'has_discounts': {'type': 'boolean'},
        'has_deposits': {'type': 'boolean'},
        'has_early_payment_discount': {'type': 'boolean'},
        'has_shipping_line': {'type': 'boolean'},
        'line_count': {'type': 'integer'},
        'complexity': {'type': 'string', 'enum': ['simple', 'complex']},
        'number_format': {'type': 'string', 'enum': ['dot_decimal', 'comma_decimal']},
        'date_format_detected': {'type': ['string', 'null']},
        'tax_info_per_line': {'type': 'string', 'enum': ['rate_only', 'amount_only', 'rate_and_amount', 'none']},
        'has_tax_summary_table': {'type': 'boolean'},
    },
    'required': ['pricing_mode', 'line_count'],
}

_LINE_SCHEMA = {
    'type': 'object',
    'properties': {
        'description': {'type': ['string', 'null']},
        'product_code': {'type': ['string', 'null']},
        'unit_of_measure': {'type': ['string', 'null']},
        'quantity': {'type': ['number', 'null']},
        'unit_price': {'type': ['number', 'null']},
        'unit_price_is_tax_included': {'type': 'boolean'},
        'discount_percent': {'type': ['number', 'null']},
        'discount_amount': {'type': ['number', 'null']},
        'subtotal_untaxed': {'type': ['number', 'null']},
        'subtotal_tax_included': {'type': ['number', 'null']},
        'tax_rate': {'type': ['number', 'null']},
        'tax_amount': {'type': ['number', 'null']},
        'is_shipping_line': {'type': 'boolean'},
        'suggested_account_category': {
            'type': ['string', 'null'],
            'enum': [
                None,
                '',
                'consulting',
                'it_services',
                'office_supplies',
                'shipping',
                'freight',
                'telecom',
                'insurance',
                'rent',
                'maintenance',
                'advertising',
                'travel',
                'training',
                'cleaning',
                'subscriptions',
                'software',
                'legal',
                'accounting',
                'bank_fees',
                'utilities',
                'raw_materials',
                'merchandise',
                'subcontracting',
            ],
        },
        'confidence': {'type': 'number'},
    },
    'required': ['description', 'confidence'],
}

EXTRACTION_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'document_type': {
            'type': 'string',
            'enum': ['invoice', 'credit_note', 'proforma', 'unknown'],
        },
        'is_marked_paid': {'type': 'boolean'},
        'vendor': _VENDOR_SCHEMA,
        'buyer': _BUYER_SCHEMA,
        'invoice': _INVOICE_SCHEMA,
        'totals': _TOTALS_SCHEMA,
        'tax_lines': {'type': 'array', 'items': _TAX_LINE_SCHEMA},
        'table_analysis': _TABLE_ANALYSIS_SCHEMA,
        'lines': {'type': 'array', 'items': _LINE_SCHEMA},
    },
    'required': ['document_type', 'vendor', 'invoice', 'totals', 'table_analysis'],
}
