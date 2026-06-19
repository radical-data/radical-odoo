"""Account matching for invoice lines.

Assigns ``account.account`` records to invoice lines using a five-tier
strategy: vendor history + description similarity, AI category mapping,
vendor default account, partner property, and fallback expense account.
"""

import logging
import re

_logger = logging.getLogger(__name__)


def _account_company_domain(env, company):
    """Return the company domain tuple for account.account.

    Odoo 19 replaced company_id with company_ids (many2many).
    """
    if 'company_ids' in env['account.account']._fields:
        return ('company_ids', 'in', company.id)
    return ('company_id', '=', company.id)


def _account_active_filter(env):
    """Return the active/non-deprecated filter tuple for account.account.

    Odoo 19 removed ``deprecated`` in favour of ``active``.
    """
    if 'deprecated' in env['account.account']._fields:
        return ('deprecated', '=', False)
    return ('active', '=', True)


# ---------------------------------------------------------------------------
# Category → account code prefix mapping (common European charts)
# Used as tier 2 when no vendor-specific description match is found.
# ---------------------------------------------------------------------------
_ACCOUNT_CATEGORY_MAP = {
    'consulting': ['6226', '6227', '622'],
    'it_services': ['6226', '6236', '622'],
    'office_supplies': ['6064', '606'],
    'shipping': ['6241', '624'],
    'freight': ['6241', '624'],
    'telecom': ['6262', '626'],
    'insurance': ['6161', '616'],
    'rent': ['6132', '613'],
    'maintenance': ['6155', '615'],
    'advertising': ['6231', '623'],
    'travel': ['6256', '625'],
    'training': ['6333', '633'],
    'cleaning': ['6152', '615'],
    'subscriptions': ['6185', '618'],
    'software': ['6186', '618', '205'],
    'legal': ['6226', '622'],
    'accounting': ['6226', '622'],
    'bank_fees': ['6270', '627'],
    'utilities': ['6061', '606'],
    'raw_materials': ['6011', '601'],
    'merchandise': ['6071', '607'],
    'subcontracting': ['6112', '611'],
}


def match_account(env, suggested_category, line_description, company, partner=None, cache=None):
    """Match an invoice line to the best ``account.account``.

    Five-tier search strategy:
    1. Vendor history with similar description (keyword overlap).
    2. Category mapping — ``_ACCOUNT_CATEGORY_MAP`` prefix lookup.
    3. Vendor default — most frequently used account for this vendor.
    4. Partner default — ``property_account_expense_id`` on the partner.
    5. Fallback — first active expense account of the company.

    Category mapping (tier 2) is prioritised over vendor default (tier 3)
    so that the AI-detected line type (e.g. shipping vs merchandise) is
    respected even when the vendor's history is dominated by a single
    account.

    Returns:
        ``account.account`` recordset (single) or ``None``.
    """
    # 1. Vendor history + description similarity
    if partner and line_description:
        acc = _match_account_from_vendor_history(env, line_description, company, partner, cache=cache)
        if acc:
            return acc

    # 2. Category mapping (promoted over vendor default)
    if suggested_category:
        acc = _match_account_by_category(env, suggested_category, company)
        if acc:
            return acc

    # 3. Vendor default account (most used)
    if partner:
        acc = _get_vendor_default_account(env, company, partner, cache=cache)
        if acc:
            return acc

    # 4. Partner default (property_account_expense_id — removed in Odoo 19)
    acc = _match_account_from_partner_property(partner, company)
    if acc:
        return acc

    # 5. Fallback — first expense account
    return _match_account_fallback_expense(env, company)


def _match_account_from_partner_property(partner, company):
    """Tier 4: partner's ``property_account_expense_id`` (removed in Odoo 19)."""
    if not partner or 'property_account_expense_id' not in partner._fields:
        return None
    prop_acc = partner.property_account_expense_id
    if not prop_acc:
        return None
    # Odoo 19: company_ids (many2many); older: company_id (many2one)
    if hasattr(prop_acc, 'company_ids'):
        return prop_acc if company in prop_acc.company_ids else None
    return prop_acc if prop_acc.company_id == company else None


def _match_account_fallback_expense(env, company):
    """Tier 5: first active expense account of the company."""
    expense_domain = [
        _account_company_domain(env, company),
        _account_active_filter(env),
    ]
    if 'account_type' in env['account.account']._fields:
        expense_domain.append(('account_type', 'in', ['expense', 'expense_direct_cost', 'expense_depreciation']))
    fallback = env['account.account'].search(expense_domain, order='code', limit=1)
    return fallback or None


def _match_account_from_vendor_history(env, description, company, partner, cache=None):
    """Tier 1: find an account from this vendor's past invoices with similar description."""
    if cache:
        past_lines = cache.get_vendor_past_lines(env, partner, company)
    else:
        past_lines = env['account.move.line'].search(
            [
                ('partner_id', '=', partner.id),
                ('move_id.move_type', '=', 'in_invoice'),
                ('move_id.state', '=', 'posted'),
                ('company_id', '=', company.id),
                ('display_type', 'in', [False, 'product']),
            ],
            order='create_date desc',
            limit=200,
        )
    if not past_lines:
        return None

    # Extract keywords from description (words >= 3 chars)
    desc_words = set(w.lower() for w in re.findall(r'\w+', description) if len(w) >= 3)
    if not desc_words:
        return None

    best_account = None
    best_overlap = 0
    for line in past_lines:
        if not line.name:
            continue
        line_words = set(w.lower() for w in re.findall(r'\w+', line.name) if len(w) >= 3)
        overlap = len(desc_words & line_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_account = line.account_id
    # Require at least 1 keyword match
    if best_overlap >= 1 and best_account:
        return best_account
    return None


def _get_vendor_default_account(env, company, partner, cache=None):
    """Tier 3: most frequently used account for this vendor."""
    if cache:
        past_lines = cache.get_vendor_past_lines(env, partner, company)
    else:
        past_lines = env['account.move.line'].search(
            [
                ('partner_id', '=', partner.id),
                ('move_id.move_type', '=', 'in_invoice'),
                ('move_id.state', '=', 'posted'),
                ('company_id', '=', company.id),
                ('display_type', 'in', [False, 'product']),
            ],
            order='create_date desc',
            limit=200,
        )
    if not past_lines:
        return None

    account_counts = {}
    for line in past_lines:
        aid = line.account_id.id
        account_counts[aid] = account_counts.get(aid, 0) + 1

    if account_counts:
        best_id = max(account_counts, key=account_counts.get)
        return env['account.account'].browse(best_id)
    return None


def _match_account_by_category(env, category, company):
    """Tier 2: match suggested category to an account via prefix mapping."""
    category_lower = category.lower().replace(' ', '_').replace('-', '_')
    prefixes = _ACCOUNT_CATEGORY_MAP.get(category_lower)
    if not prefixes:
        return None

    for prefix in prefixes:
        account = env['account.account'].search(
            [
                _account_company_domain(env, company),
                ('code', '=like', prefix + '%'),
                _account_active_filter(env),
                ('account_type', 'in', ('expense', 'expense_direct_cost')),
            ],
            order='code',
            limit=1,
        )
        if account:
            return account
    return None
