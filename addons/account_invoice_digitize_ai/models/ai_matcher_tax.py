"""Tax matching for invoice extraction.

Matches extracted tax rates to ``account.tax`` records using vendor
history (preferred taxes) then exact/approximate rate matching.
"""

import logging

_logger = logging.getLogger(__name__)


def _get_vendor_taxes(env, partner, company, cache=None):
    """Return active purchase taxes used in past invoices for *partner*.

    Uses *cache* when available (batched extraction), otherwise queries
    the database directly.
    """
    if cache:
        return cache.get_vendor_taxes(env, partner, company)
    past_lines = env['account.move.line'].search(
        [
            ('partner_id', '=', partner.id),
            ('move_id.move_type', '=', 'in_invoice'),
            ('move_id.state', '=', 'posted'),
            ('company_id', '=', company.id),
        ],
        limit=200,
    )
    tax_ids = set()
    for line in past_lines:
        tax_ids.update(line.tax_ids.ids)
    if not tax_ids:
        return env['account.tax']
    return env['account.tax'].search(
        [
            ('id', 'in', list(tax_ids)),
            ('type_tax_use', '=', 'purchase'),
            ('active', '=', True),
        ]
    )


def _match_tax_from_vendor(env, rate, partner, company, cache):
    """Step 1: check vendor history for a tax matching *rate*."""
    vendor_taxes = _get_vendor_taxes(env, partner, company, cache=cache)
    for tax in vendor_taxes:
        if tax.amount == rate:
            return tax
    for tax in vendor_taxes:
        if abs(tax.amount - rate) < 0.5:
            return tax
    return None


def match_tax_by_rate(env, rate, company, partner=None, cache=None):
    """Find a purchase tax matching the given rate.

    Search strategy:
    1. If a partner is given, check which taxes have been used on past
       invoices for this vendor — prefer those.
    2. Exact rate match among active purchase taxes.
    3. Approximate match within ±0.5 percentage points.

    Returns:
        account.tax recordset (single) or ``None``.
    """
    # 1. Vendor history — prefer taxes already used for this vendor
    if partner:
        hit = _match_tax_from_vendor(env, rate, partner, company, cache)
        if hit:
            return hit

    # 2. Exact rate match (from cached all-taxes list)
    if cache:
        all_taxes = cache.get_all_purchase_taxes(env, company)
    else:
        all_taxes = env['account.tax'].search(
            [
                ('company_id', '=', company.id),
                ('type_tax_use', '=', 'purchase'),
                ('active', '=', True),
            ]
        )
    exact = all_taxes.filtered(lambda t: t.amount == rate)
    if exact:
        return exact[0]

    # 3. Approximate match (within 0.5%)
    for tax in all_taxes:
        if abs(tax.amount - rate) < 0.5:
            return tax

    return None
