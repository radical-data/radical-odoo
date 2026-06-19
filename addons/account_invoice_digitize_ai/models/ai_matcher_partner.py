"""Partner matching for invoice extraction.

Matches extracted vendor data to ``res.partner`` records using a
multi-tier strategy: VAT number, exact/partial name, token-based fuzzy
matching, and email.
"""

import logging
import re

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token-based fuzzy company name matching
# ---------------------------------------------------------------------------

_LEGAL_SUFFIXES = {
    'sa',
    'sas',
    'sarl',
    'srl',
    'eurl',
    'sasu',  # FR
    'gmbh',
    'ag',
    'kg',
    'ug',  # DE
    'ltd',
    'llc',
    'plc',
    'inc',
    'corp',
    'co',  # EN
    'bv',
    'nv',
    'spa',
    'ab',
    'oy',
    'as',
    'aps',
    'lda',
}
_TOKEN_MATCH_THRESHOLD = 0.5


def _normalize_company_name(name):
    """Normalize a company name to a set of meaningful tokens.

    Lowercases, removes punctuation, strips legal suffixes (SA, GmbH, etc.).
    Returns a frozenset of remaining tokens (length >= 2).
    """
    if not name:
        return frozenset()
    # Lowercase and extract word tokens
    tokens = re.findall(r'\w+', name.lower())
    # Remove legal suffixes
    return frozenset(t for t in tokens if t not in _LEGAL_SUFFIXES and len(t) >= 2)


def _token_match_score(tokens_a, tokens_b):
    """Compute asymmetric Jaccard score: overlap / min(len_a, len_b).

    Returns 0.0 if either set is empty.
    """
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    return overlap / min(len(tokens_a), len(tokens_b))


def _match_partner_by_tokens(env, name):
    """Token-based fuzzy matching for company names.

    Pre-filters candidates via ilike on the longest token, then scores
    all candidates using asymmetric Jaccard on normalized tokens.

    Returns:
        res.partner recordset (single) or None.
    """
    tokens = _normalize_company_name(name)
    if not tokens:
        return None

    # Use the longest token for DB pre-filter
    longest_token = max(tokens, key=len)
    candidates = env['res.partner'].search(
        [
            ('active', '=', True),
            ('is_company', '=', True),
            ('name', 'ilike', longest_token),
        ],
        limit=50,
    )
    if not candidates:
        return None

    best_partner = None
    best_score = 0.0
    for partner in candidates:
        partner_tokens = _normalize_company_name(partner.name)
        score = _token_match_score(tokens, partner_tokens)
        if score > best_score:
            best_score = score
            best_partner = partner

    if best_score >= _TOKEN_MATCH_THRESHOLD:
        return best_partner
    return None


def match_partner(env, vendor_data):
    """Match extracted vendor data to a res.partner record.

    Search strategy (in order of reliability):
    1. By VAT number (exact, case-insensitive)
    2a. By name (exact case-insensitive, then partial ilike)
    2b. By name tokens (fuzzy, legal suffixes stripped)
    3. By email

    Args:
        env: Odoo environment (``self.env``).
        vendor_data: dict with keys ``vat``, ``name``, ``email``, etc.

    Returns:
        res.partner recordset (single) or ``None``.
    """
    Partner = env['res.partner']

    # 1. By VAT number (most reliable)
    vat = vendor_data.get('vat')
    if vat:
        vat_clean = vat.replace(' ', '').upper()
        partner = Partner.search([('vat', '=ilike', vat_clean), ('active', '=', True)], limit=1)
        if partner:
            return partner

    # 2a. By name (exact then partial in one query)
    name = vendor_data.get('name')
    if name:
        partners = Partner.search(
            [
                ('active', '=', True),
                ('is_company', '=', True),
                '|',
                ('name', '=ilike', name),
                ('name', 'ilike', name),
            ],
            limit=5,
        )
        if partners:
            # Prefer exact case-insensitive match
            name_lower = name.lower()
            for p in partners:
                if p.name and p.name.lower() == name_lower:
                    return p
            return partners[0]

        # 2b. Token-based fuzzy matching (handles abbreviations, reordering)
        partner = _match_partner_by_tokens(env, name)
        if partner:
            return partner

    # 3. By email
    email = vendor_data.get('email')
    if email:
        partner = Partner.search([('email', '=ilike', email), ('active', '=', True)], limit=1)
        if partner:
            return partner

    return None
