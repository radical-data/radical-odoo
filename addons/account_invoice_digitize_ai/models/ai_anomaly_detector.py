"""Invoice amount anomaly detection.

Compares the AI-extracted total against the vendor's historical invoice
amounts.  Flags unusually high or low amounts that may indicate an
extraction error (misread decimal separator, missing digit) or a
genuine billing anomaly.
"""

import logging
import statistics
from datetime import timedelta

from odoo import fields

_logger = logging.getLogger(__name__)

MIN_HISTORY = 3
LOOKBACK_DAYS = 730  # 2 years
HISTORY_LIMIT = 50  # max past invoices to fetch
HIGH_RATIO = 3.0  # flag if amount > 3× average
LOW_RATIO = 0.1  # flag if amount < 10% of average


def detect_anomalies(env, partner, total_amount, company=None):
    """Check if the extracted amount is anomalous for this vendor.

    Requires at least ``MIN_HISTORY`` posted invoices in the last
    ``LOOKBACK_DAYS`` days.  Uses mean and ratio thresholds — not
    standard deviation — to avoid false positives with low sample sizes.

    Args:
        env: Odoo environment.
        partner: ``res.partner`` record or *None*.
        total_amount: Extracted total amount (float or *None*).
        company: ``res.company`` record or *None* (defaults to env.company).

    Returns:
        dict with keys ``found``, ``message``, ``vendor_avg``,
        ``ratio``.  Empty dict if insufficient data.
    """
    if not partner or not total_amount:
        return {}

    if not company:
        company = env.company

    cutoff = fields.Date.today() - timedelta(days=LOOKBACK_DAYS)
    past = env['account.move'].search(
        [
            ('partner_id', '=', partner.id),
            ('company_id', '=', company.id),
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', cutoff),
        ],
        order='invoice_date desc',
        limit=HISTORY_LIMIT,
    )

    amounts = [inv.amount_total for inv in past if inv.amount_total > 0]
    if len(amounts) < MIN_HISTORY:
        return {}

    avg = statistics.mean(amounts)
    if avg <= 0:
        return {}

    ratio = total_amount / avg

    if ratio > HIGH_RATIO:
        msg = env._('Amount %.2f is %.1f× higher than vendor average (%.2f)') % (total_amount, ratio, avg)
        _logger.warning('Anomaly detection: %s', msg)
        return {
            'found': True,
            'message': msg,
            'vendor_avg': round(avg, 2),
            'ratio': round(ratio, 1),
        }

    if ratio < LOW_RATIO:
        msg = env._('Amount %.2f is unusually low compared to vendor average (%.2f)') % (total_amount, avg)
        _logger.warning('Anomaly detection: %s', msg)
        return {
            'found': True,
            'message': msg,
            'vendor_avg': round(avg, 2),
            'ratio': round(ratio, 1),
        }

    return {}
