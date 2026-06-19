"""Purchase order and product matching for invoice extraction.

Requires the ``purchase`` module to be installed — all functions
gracefully return ``None`` when it is not available.
"""

import re
from datetime import timedelta

_PO_PREFIXES = ('P.O.', 'PO#', 'PO ', 'PO-', 'PO:', 'P.O ')


# ---------------------------------------------------------------------------
# Product matching from vendor product codes
# ---------------------------------------------------------------------------


def _resolve_supplierinfo_product(sinfo):
    """Return the ``product.product`` linked to a supplier info record, or ``None``."""
    if not sinfo:
        return None
    if sinfo.product_id:
        return sinfo.product_id
    if sinfo.product_tmpl_id:
        variants = sinfo.product_tmpl_id.product_variant_ids
        return variants[0] if variants else None
    return None


def match_product(env, product_code, description, partner=None):
    """Match an extracted product code to a ``product.product`` record.

    Search strategy (in order of reliability):
    1. Vendor product code in ``product.supplierinfo`` (vendor-specific).
    2. Vendor product code in ``product.supplierinfo`` (any vendor).
    3. Internal reference (``default_code``) in ``product.product``.

    Returns:
        ``product.product`` recordset (single) or ``None``.
    """
    if not product_code:
        return None

    code = product_code.strip()
    if not code:
        return None

    SupplierInfo = env['product.supplierinfo']

    # 1. Vendor-specific supplier info
    if partner:
        sinfo = SupplierInfo.search(
            [('partner_id', '=', partner.id), ('product_code', '=ilike', code)],
            limit=1,
        )
        product = _resolve_supplierinfo_product(sinfo)
        if product:
            return product

    # 2. Any vendor supplier info
    sinfo = SupplierInfo.search([('product_code', '=ilike', code)], limit=1)
    product = _resolve_supplierinfo_product(sinfo)
    if product:
        return product

    # 3. Internal reference (default_code)
    return env['product.product'].search([('default_code', '=ilike', code)], limit=1) or None


# ---------------------------------------------------------------------------
# Purchase order matching (optional — requires purchase module)
# ---------------------------------------------------------------------------


def _is_purchase_installed(env):
    """Check at runtime if the ``purchase`` module is installed."""
    return 'purchase.order' in env


def _normalize_po_ref(ref):
    """Normalize a PO reference for fuzzy matching.

    Strips whitespace, common prefixes (PO, P.O., PO#), and leading zeros.
    Returns an uppercased string for comparison.
    """
    if not ref:
        return ''
    normalized = ref.strip().upper()
    if not normalized:
        return ''
    for prefix in _PO_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    return normalized.lstrip('0') or '0'


def match_purchase_order(env, po_ref, partner=None, company=None, total_amount=None, invoice_date=None):
    """Match an extracted PO reference to a ``purchase.order`` record.

    Requires the ``purchase`` module to be installed — gracefully returns
    ``(None, None)`` if not available.

    Search strategy:
    1. Exact match by ``purchase.order.name`` + partner + company.
    2. Fuzzy match (normalized reference, ``ilike``).
    3. By vendor + amount (±10%) + date range (±30 days).

    Returns:
        Tuple ``(purchase_order, match_tier)`` where *match_tier* is
        ``'exact'``, ``'fuzzy'``, ``'amount_date'``, or ``None``.
    """
    if not _is_purchase_installed(env):
        return None, None
    if not po_ref and not (partner and total_amount):
        return None, None

    PO = env['purchase.order']
    company_id = company.id if company else env.company.id
    base_domain = [
        ('company_id', '=', company_id),
        ('state', 'in', ('purchase', 'done')),
    ]

    if po_ref:
        result = _match_po_by_ref(PO, po_ref, base_domain, partner)
        if result:
            return result

    if partner and total_amount:
        result = _match_po_by_amount(PO, base_domain, partner, total_amount, invoice_date)
        if result:
            return result

    return None, None


def _match_po_by_ref(PO, po_ref, base_domain, partner):
    """Tier 1 (exact) and Tier 2 (fuzzy) PO matching by reference."""
    # Tier 1: exact match
    domain = list(base_domain) + [('name', '=ilike', po_ref.strip())]
    if partner:
        domain.append(('partner_id', '=', partner.id))
    po = PO.search(domain, limit=1)
    if po:
        return po, 'exact'

    # Tier 2: fuzzy (normalized)
    normalized = _normalize_po_ref(po_ref)
    if not normalized:
        return None, None
    domain = list(base_domain) + [('name', 'ilike', normalized)]
    if partner:
        domain.append(('partner_id', '=', partner.id))
    orders = PO.search(domain, limit=5)
    for order in orders:
        if _normalize_po_ref(order.name) == normalized:
            return order, 'fuzzy'
    if orders:
        return orders[0], 'fuzzy'
    return None, None


def _match_po_by_amount(PO, base_domain, partner, total_amount, invoice_date):
    """Tier 3: match PO by vendor + amount proximity + date range."""
    tolerance = abs(total_amount) * 0.10
    domain = list(base_domain) + [
        ('partner_id', '=', partner.id),
        ('amount_total', '>=', total_amount - tolerance),
        ('amount_total', '<=', total_amount + tolerance),
    ]
    if invoice_date:
        try:
            from odoo import fields as odoo_fields

            date_obj = odoo_fields.Date.to_date(invoice_date)
            if date_obj:
                domain += [
                    ('date_order', '>=', date_obj - timedelta(days=30)),
                    ('date_order', '<=', date_obj + timedelta(days=30)),
                ]
        except (ValueError, TypeError):
            pass
    orders = PO.search(domain, order='date_order desc', limit=1)
    if orders:
        return orders, 'amount_date'
    return None, None


def match_purchase_order_line(env, po, line_data, partner=None):
    """Match an invoice line to a ``purchase.order.line``.

    Strategies (in order):
    1. Product code → product match → PO line with same product.
    2. Description keyword overlap (≥2 common words of 3+ chars).
    3. Quantity + unit price proximity (qty exact, price ±5%).

    Returns:
        ``purchase.order.line`` recordset (single) or ``None``.
    """
    if not _is_purchase_installed(env) or not po:
        return None

    po_lines = po.order_line.filtered(lambda ln: ln.product_qty > 0)
    if not po_lines:
        return None

    # Strategy 1: product code
    match = _match_pol_by_product(env, po_lines, line_data, partner)
    if match:
        return match

    # Strategy 2: description keyword overlap
    description = line_data.get('description', '')
    if description:
        match = _match_pol_by_description(po_lines, description)
        if match:
            return match

    # Strategy 3: quantity + unit price
    return _match_pol_by_qty_price(po_lines, line_data)


def _match_pol_by_product(env, po_lines, line_data, partner):
    """Match a PO line by product code → product → PO line with same product."""
    product_code = line_data.get('product_code')
    if not product_code:
        return None
    product = match_product(env, product_code, '', partner=partner)
    if not product:
        return None
    for pol in po_lines:
        if pol.product_id == product:
            return pol
    return None


def _match_pol_by_qty_price(po_lines, line_data):
    """Match a PO line by quantity (exact) and unit price (±5%)."""
    qty = line_data.get('quantity')
    price = line_data.get('unit_price')
    if not qty or not price:
        return None
    for pol in po_lines:
        qty_ok = abs(pol.product_qty - qty) < 0.01
        price_ok = abs(pol.price_unit - price) < max(0.01, abs(price) * 0.05)
        if qty_ok and price_ok:
            return pol
    return None


def _match_pol_by_description(po_lines, description):
    """Find the PO line with best keyword overlap to *description*."""
    desc_words = {w.lower() for w in re.findall(r'\w+', description) if len(w) >= 3}
    if not desc_words:
        return None
    best_line = None
    best_overlap = 0
    for pol in po_lines:
        pol_words = {w.lower() for w in re.findall(r'\w+', pol.name or '') if len(w) >= 3}
        overlap = len(desc_words & pol_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_line = pol
    return best_line if best_overlap >= 2 else None
