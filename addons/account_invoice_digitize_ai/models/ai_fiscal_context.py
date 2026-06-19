"""Fiscal context builder for AI extraction prompts.

Builds the fiscal context string (chart of accounts, taxes, vendor history)
injected into the AI prompt. Uses a three-tier account prioritization:
  1. Vendor-specific account history (highest priority)
  2. Company-wide frequently used accounts
  3. Full chart of accounts (fallback for new companies)
"""

import datetime
import logging
import re
import threading

from .ai_matcher_account import _account_active_filter, _account_company_domain
from .ai_prompt import (
    ACCOUNT_SECTION_NEW_COMPANY,
    ACCOUNT_SECTION_NO_VENDOR,
    ACCOUNT_SECTION_VENDOR,
    FISCAL_CONTEXT_TAX_ONLY_TEMPLATE,
    FISCAL_CONTEXT_TEMPLATE,
)

_logger = logging.getLogger(__name__)

VENDOR_HISTORY_LIMIT = 200
COMPANY_HISTORY_LIMIT = 500
ACCOUNT_DISPLAY_LIMIT = 30

# ---------------------------------------------------------------------------
# Company-level cache (accounts + taxes) — auto-expires daily
# Key: (company_id, date_str) → dict with account IDs + tax list string
# ---------------------------------------------------------------------------
_fiscal_cache = {}
_fiscal_cache_lock = threading.Lock()


def invalidate_fiscal_cache(company_id=None):
    """Clear the fiscal context cache.

    Args:
        company_id: If provided, only clear cache for this company.
            If None, clear all entries.
    """
    with _fiscal_cache_lock:
        if company_id:
            stale_keys = [k for k in _fiscal_cache if k[0] == company_id]
            for k in stale_keys:
                del _fiscal_cache[k]
        else:
            _fiscal_cache.clear()


def _get_company_cache(env, company):
    """Return cached company-level fiscal data (accounts + taxes).

    Cache key is ``(company_id, today)``.  Stale entries from previous
    days are evicted automatically.
    """
    today = str(datetime.date.today())
    cache_key = (company.id, today)

    # Fast path: check cache under lock
    with _fiscal_cache_lock:
        if cache_key in _fiscal_cache:
            return _fiscal_cache[cache_key]

    # Compute outside lock (DB queries are slow, don't hold lock)
    # Expense accounts
    expense_domain = [
        _account_company_domain(env, company),
        _account_active_filter(env),
        ('account_type', 'in', ['expense', 'expense_direct_cost', 'expense_depreciation']),
    ]

    all_expense_accounts = env['account.account'].search(expense_domain, order='code')

    # Purchase taxes
    taxes = env['account.tax'].search(
        [
            ('company_id', '=', company.id),
            ('type_tax_use', '=', 'purchase'),
            ('active', '=', True),
        ],
        order='amount',
    )
    tax_list = '\n'.join('- %s: %s%%' % (t.name, t.amount) for t in taxes) or '(no purchase taxes configured)'

    # Cache IDs (not recordsets) to avoid cursor issues across requests
    entry = {
        'expense_account_ids': all_expense_accounts.ids,
        'tax_list_str': tax_list,
    }

    # Double-checked locking: re-check + evict + store in a single lock
    with _fiscal_cache_lock:
        if cache_key not in _fiscal_cache:
            # Evict stale entries (previous days) for this company
            stale_keys = [k for k in _fiscal_cache if k[0] == company.id and k[1] != today]
            for k in stale_keys:
                del _fiscal_cache[k]
            _fiscal_cache[cache_key] = entry
        return _fiscal_cache[cache_key]


def build_fiscal_context(env, company, vendor=None, include_accounts=True):
    """Build the fiscal context string for the AI prompt.

    Args:
        env: Odoo environment.
        company: ``res.company`` record.
        vendor: ``res.partner`` record or *None*.
        include_accounts: If *False*, only include taxes (simplified mode).

    Returns:
        Formatted fiscal context string ready for prompt injection.
    """
    country_code = company.country_id.code or 'XX'
    currency = company.currency_id.name or 'EUR'

    # Get cached company data (accounts + taxes)
    cached = _get_company_cache(env, company)
    tax_list = cached['tax_list_str']

    # Simplified mode: taxes only, no chart of accounts
    if not include_accounts:
        return FISCAL_CONTEXT_TAX_ONLY_TEMPLATE.format(
            company_name=company.name,
            country_code=country_code,
            currency=currency,
            tax_list=tax_list,
        )

    # Full mode: accounts + taxes + vendor context
    chart_name = ''
    chart_template = getattr(company, 'chart_template', None)
    if chart_template:
        chart_name = str(chart_template)
    if not chart_name:
        chart_name = 'Standard (%s)' % country_code

    all_expense_accounts = env['account.account'].browse(cached['expense_account_ids'])

    # Build account section (vendor-specific history is NOT cached)
    account_section = _build_account_section(env, vendor, all_expense_accounts, company)

    # Vendor context (sanitize name to prevent prompt injection)
    vendor_context = ''
    if vendor:
        safe_name = re.sub(r'[<>\[\]{}\\\r\n]', '', vendor.name or '')[:200]
        safe_vat = re.sub(r'[<>\[\]{}\\\r\n]', '', vendor.vat or 'N/A')[:30]
        vendor_context = 'Identified vendor: %s (VAT: %s)' % (safe_name, safe_vat)

    return FISCAL_CONTEXT_TEMPLATE.format(
        company_name=company.name,
        country_code=country_code,
        currency=currency,
        chart_name=chart_name,
        account_section=account_section,
        tax_list=tax_list,
        vendor_context=vendor_context,
    )


def _build_account_section(env, vendor, all_accounts, company):
    """Build the prioritized account list for the prompt.

    Three-tier logic:
      - Tier 1 (vendor identified): vendor-specific + company-wide history
      - Tier 2 (no vendor, has history): company-wide + remaining accounts
      - Tier 3 (new company): full chart of accounts
    """
    # Tier 1: vendor-specific account history
    if vendor:
        vendor_lines = env['account.move.line'].search(
            [
                ('partner_id', '=', vendor.id),
                ('move_id.move_type', '=', 'in_invoice'),
                ('move_id.state', '=', 'posted'),
                ('account_id', 'in', all_accounts.ids),
                ('company_id', '=', company.id),
            ],
            order='create_date desc',
            limit=VENDOR_HISTORY_LIMIT,
        )
        vendor_accounts = vendor_lines.mapped('account_id')

        # Tier 2: company-wide frequently used
        company_lines = env['account.move.line'].search(
            [
                ('move_id.move_type', '=', 'in_invoice'),
                ('move_id.state', '=', 'posted'),
                ('account_id', 'in', all_accounts.ids),
                ('account_id', 'not in', vendor_accounts.ids),
                ('company_id', '=', company.id),
            ],
            order='create_date desc',
            limit=COMPANY_HISTORY_LIMIT,
        )
        company_accounts = company_lines.mapped('account_id')

        return ACCOUNT_SECTION_VENDOR.format(
            vendor_accounts=_format_accounts(vendor_accounts),
            company_accounts=_format_accounts(company_accounts),
        )

    # No vendor identified — check if company has history
    company_lines = env['account.move.line'].search(
        [
            ('move_id.move_type', '=', 'in_invoice'),
            ('move_id.state', '=', 'posted'),
            ('account_id', 'in', all_accounts.ids),
            ('company_id', '=', company.id),
        ],
        order='create_date desc',
        limit=COMPANY_HISTORY_LIMIT,
    )

    if company_lines:
        company_accounts = company_lines.mapped('account_id')
        remaining = all_accounts - company_accounts
        return ACCOUNT_SECTION_NO_VENDOR.format(
            company_accounts=_format_accounts(company_accounts),
            all_accounts=_format_accounts(remaining),
        )

    # New company — full chart
    return ACCOUNT_SECTION_NEW_COMPANY.format(
        all_accounts=_format_accounts(all_accounts),
    )


def _format_accounts(accounts, limit=ACCOUNT_DISPLAY_LIMIT):
    """Format an account recordset as a prompt-friendly list."""
    lines = []
    for acc in accounts[:limit]:
        lines.append('- %s %s' % (acc.code, acc.name))
    if len(accounts) > limit:
        lines.append('  ... and %d more' % (len(accounts) - limit))
    return '\n'.join(lines) or '(none)'
