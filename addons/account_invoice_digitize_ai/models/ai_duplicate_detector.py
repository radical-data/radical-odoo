"""Duplicate invoice detection.

Before applying AI-extracted data, checks if an invoice with the same
vendor + reference (+ date + amount) already exists in the system.
Returns a warning dict — never blocks the extraction.
"""

import logging

_logger = logging.getLogger(__name__)


def detect_duplicates(env, move, partner, ref, invoice_date, total_amount, company=None):
    """Search for existing invoices matching the extracted data.

    Two severity levels:
      - **Exact match**: partner + ref + date + amount → high risk
      - **Partial match**: partner + ref only → medium risk

    Args:
        env: Odoo environment.
        move: Current ``account.move`` record (excluded from search).
        partner: ``res.partner`` record or *None*.
        ref: Extracted invoice reference (str or *None*).
        invoice_date: Extracted date as ISO string or *None*.
        total_amount: Extracted total amount (float or *None*).
        company: ``res.company`` record or *None* (defaults to move's company).

    Returns:
        dict with keys ``found``, ``duplicate_ids``, ``message``,
        ``severity`` (``'exact'`` or ``'partial'``).
        Empty dict if no duplicate or insufficient data.
    """
    if not partner or not ref:
        return {}

    if not company:
        company = move.company_id or env.company

    base_domain = [
        ('partner_id', '=', partner.id),
        ('company_id', '=', company.id),
        ('ref', '=ilike', ref.strip()),
        ('id', '!=', move.id),
        ('move_type', 'in', ('in_invoice', 'in_refund')),
    ]

    # --- Exact match: partner + ref + date + amount ---------------------
    if invoice_date and total_amount is not None:
        exact_domain = base_domain + [
            ('invoice_date', '=', invoice_date),
            ('amount_total', '>=', total_amount - 0.05),
            ('amount_total', '<=', total_amount + 0.05),
        ]
        exact = env['account.move'].search(exact_domain, limit=5)
        if exact:
            msg = env._('Possible duplicate: %d invoice(s) found with same vendor, reference, date and amount') % len(
                exact
            )
            _logger.warning('Duplicate detection: %s (ids=%s)', msg, exact.ids)
            return {
                'found': True,
                'duplicate_ids': exact.ids,
                'message': msg,
                'severity': 'exact',
            }

    # --- Partial match: partner + ref only ------------------------------
    partial = env['account.move'].search(base_domain, limit=5)
    if partial:
        msg = env._("Invoice reference '%s' already exists for this vendor (%d invoice(s))") % (ref, len(partial))
        _logger.info('Duplicate detection (partial): %s (ids=%s)', msg, partial.ids)
        return {
            'found': True,
            'duplicate_ids': partial.ids,
            'message': msg,
            'severity': 'partial',
        }

    return {}
