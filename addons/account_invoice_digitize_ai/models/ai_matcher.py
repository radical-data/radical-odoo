"""Matching facade — re-exports all matching functions from submodules.

Consumers import ``from . import ai_matcher`` and use
``ai_matcher.match_partner()``, ``ai_matcher.match_tax_by_rate()``, etc.
Submodules handle each matching domain independently:

- ``ai_matcher_partner``: partner matching (VAT, name, email, token fuzzy)
- ``ai_matcher_tax``: tax matching (vendor history, exact/approximate rate)
- ``ai_matcher_account``: account matching (5-tier strategy, category map)
- ``ai_matcher_po``: purchase order and product matching

This module also defines ``VendorMatchCache`` (used across tax and
account matching) and payment term matching (too small for a separate file).
"""

import logging
import re

from .ai_matcher_account import (  # noqa: F401
    _ACCOUNT_CATEGORY_MAP,
    _account_active_filter,
    _account_company_domain,
    _get_vendor_default_account,
    _match_account_by_category,
    _match_account_fallback_expense,
    _match_account_from_partner_property,
    _match_account_from_vendor_history,
    match_account,
)
from .ai_matcher_partner import (  # noqa: F401
    _LEGAL_SUFFIXES,
    _TOKEN_MATCH_THRESHOLD,
    _match_partner_by_tokens,
    _normalize_company_name,
    _token_match_score,
    match_partner,
)
from .ai_matcher_po import (  # noqa: F401
    _is_purchase_installed,
    _normalize_po_ref,
    match_product,
    match_purchase_order,
    match_purchase_order_line,
)
from .ai_matcher_tax import (  # noqa: F401
    _get_vendor_taxes,
    _match_tax_from_vendor,
    match_tax_by_rate,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-extraction cache — avoids N identical DB queries per invoice line
# ---------------------------------------------------------------------------


_VENDOR_LINE_HISTORY_LIMIT = 200
_PAYMENT_TERM_HISTORY_LIMIT = 50


class VendorMatchCache:
    """Cache vendor history queries across invoice lines of a single extraction.

    Without caching, an invoice with N lines triggers 3×N identical DB queries
    (vendor taxes, account history, default account).  This cache reduces that
    to 3 queries total per (partner, company) pair.
    """

    def __init__(self):
        self._data = {}

    def get_vendor_past_lines(self, env, partner, company):
        """Cached: past product lines for this vendor (display_type filtered)."""
        key = ('past_lines', partner.id, company.id)
        if key not in self._data:
            self._data[key] = env['account.move.line'].search(
                [
                    ('partner_id', '=', partner.id),
                    ('move_id.move_type', '=', 'in_invoice'),
                    ('move_id.state', '=', 'posted'),
                    ('company_id', '=', company.id),
                    ('display_type', 'in', [False, 'product']),
                ],
                order='create_date desc',
                limit=_VENDOR_LINE_HISTORY_LIMIT,
            )
        return self._data[key]

    def get_vendor_taxes(self, env, partner, company):
        """Cached: active purchase taxes used in past invoices for this vendor."""
        key = ('taxes', partner.id, company.id)
        if key not in self._data:
            past_lines = self.get_vendor_past_lines(env, partner, company)
            tax_ids = set(past_lines.mapped('tax_ids').ids)
            if not tax_ids:
                self._data[key] = env['account.tax']
            else:
                self._data[key] = env['account.tax'].search(
                    [
                        ('id', 'in', list(tax_ids)),
                        ('type_tax_use', '=', 'purchase'),
                        ('active', '=', True),
                    ]
                )
        return self._data[key]

    def get_all_purchase_taxes(self, env, company):
        """Cached: all active purchase taxes for a company."""
        key = ('all_taxes', company.id)
        if key not in self._data:
            self._data[key] = env['account.tax'].search(
                [
                    ('company_id', '=', company.id),
                    ('type_tax_use', '=', 'purchase'),
                    ('active', '=', True),
                ]
            )
        return self._data[key]


# ---------------------------------------------------------------------------
# Payment terms matching
# ---------------------------------------------------------------------------

_DAY_COUNT_RE = re.compile(r'(\d+)\s*(?:jours?|days?|tage?|giorni|días?)', re.IGNORECASE)


def _get_vendor_payment_term(env, partner, company):
    """Return the most frequently used payment term for *partner*, or ``None``."""
    if not partner:
        return None
    past_moves = env['account.move'].search(
        [
            ('partner_id', '=', partner.id),
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
            ('company_id', '=', company.id),
            ('invoice_payment_term_id', '!=', False),
        ],
        order='create_date desc',
        limit=_PAYMENT_TERM_HISTORY_LIMIT,
    )
    if not past_moves:
        return None
    # mapped() with dotted path returns a list (preserving duplicates),
    # unlike mapped() on a relational field which returns a unique recordset.
    term_counts = {}
    for tid in past_moves.mapped('invoice_payment_term_id.id'):
        term_counts[tid] = term_counts.get(tid, 0) + 1
    best_id = max(term_counts, key=term_counts.get)
    return env['account.payment.term'].browse(best_id)


def match_payment_term(env, payment_terms_text, company, partner=None):
    """Match extracted payment terms text to an ``account.payment.term``.

    Search strategy (in order of priority):
    1. Vendor history — payment term most frequently used for this vendor.
    2. Fuzzy name match — substring case-insensitive on payment term name.
    3. Day-count heuristic — extract number of days from text and match
       against payment term names.

    Args:
        env: Odoo environment.
        payment_terms_text: Raw payment terms string from the invoice
            (e.g. ``"30 jours fin de mois"``).
        company: ``res.company`` record.
        partner: Optional ``res.partner`` record for vendor-aware matching.

    Returns:
        ``account.payment.term`` recordset (single) or ``None``.
    """
    if not payment_terms_text:
        return None

    # 1. Vendor history — most frequently used payment term
    vendor_term = _get_vendor_payment_term(env, partner, company)
    if vendor_term:
        return vendor_term

    all_terms = env['account.payment.term'].search(
        [('company_id', 'in', [company.id, False])],
        limit=200,
    )
    if not all_terms:
        return None

    text_lower = payment_terms_text.lower().strip()
    day_match = _DAY_COUNT_RE.search(payment_terms_text)
    day_count = day_match.group(1) if day_match else None

    # 2. Fuzzy name match (substring) + 3. Day-count heuristic (single pass)
    day_candidate = None
    for term in all_terms:
        term_name_lower = term.name.lower()
        if text_lower in term_name_lower or term_name_lower in text_lower:
            return term
        if day_count and day_candidate is None and day_count in term.name:
            day_candidate = term

    return day_candidate
