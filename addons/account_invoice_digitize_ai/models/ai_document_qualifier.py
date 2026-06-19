"""Document qualification via keyword heuristics.

Before calling the AI, scans extracted text for invoice/proforma/paid
keywords to catch obvious non-invoices early (saves API cost).  For
scanned PDFs and images, Claude handles classification via the prompt.
"""

import logging

from .ai_prompt import (
    ALL_TOTAL_PATTERNS,
    INVOICE_KEYWORDS,
    PAID_KEYWORDS,
    PROFORMA_KEYWORDS,
)

_logger = logging.getLogger(__name__)


def qualify_document(text):
    """Classify a document based on keyword heuristics.

    Scans the extracted text for invoice, proforma, and paid keywords.
    Total-amount labels (e.g. "Total TTC", "Subtotal") also count as
    evidence of an invoice.

    Args:
        text: Raw text extracted from the PDF.

    Returns:
        dict with ``is_likely_invoice``, ``is_proforma``, ``is_paid``.
    """
    if not text:
        return {'is_likely_invoice': False, 'is_proforma': False, 'is_paid': False}

    upper = text.upper()

    invoice_hits = sum(1 for kw in INVOICE_KEYWORDS if kw.upper() in upper)
    proforma_hits = sum(1 for kw in PROFORMA_KEYWORDS if kw.upper() in upper)
    paid_hits = sum(1 for kw in PAID_KEYWORDS if kw.upper() in upper)
    total_hits = sum(1 for lbl in ALL_TOTAL_PATTERNS if lbl.upper() in upper)

    is_proforma = proforma_hits > 0
    is_likely_invoice = (invoice_hits > 0 or total_hits > 0) and not is_proforma
    is_paid = paid_hits > 0

    if is_proforma:
        _logger.info('Document qualification: proforma detected (%d keyword(s))', proforma_hits)
    if is_paid:
        _logger.info('Document qualification: PAID stamp detected')
    if not is_likely_invoice and not is_proforma:
        _logger.info('Document qualification: no invoice keywords found')

    return {
        'is_likely_invoice': is_likely_invoice,
        'is_proforma': is_proforma,
        'is_paid': is_paid,
    }
