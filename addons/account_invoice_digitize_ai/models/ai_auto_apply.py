"""Auto-apply policy for AI invoice extraction.

Determines whether an extraction result can be applied automatically
(skipping the preview wizard) based on vendor reliability, confidence
scores, and document type.
"""

import logging

from odoo import models

from . import ai_matcher

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    _AI_MIN_EXTRACTIONS_FOR_RELIABILITY = 3
    _AI_MIN_RELIABILITY_RATE = 70

    def _ai_can_auto_apply(self, data):
        """Check if extraction can be auto-applied (skip preview).

        Conditions: enabled in settings, vendor matched + reliable,
        all confidence scores >= threshold, no warnings, valid doc type.
        """
        if not self._ai_get_bool_param('ai_auto_apply_enabled'):
            return False

        # Quick disqualifiers
        doc_type = data.get('document_type', '')
        if doc_type not in ('invoice', 'credit_note') or data.get('is_marked_paid'):
            return False

        # Vendor must be matched and reliable
        vendor_data = data.get('vendor', {})
        partner = ai_matcher.match_partner(self.env, vendor_data)
        if not partner or not self._ai_is_vendor_reliable(partner):
            return False
        # Stash matched partner for downstream _ai_apply_extraction
        # (avoids a redundant match_partner call in _ai_resolve_partner)
        data['_force_partner_id'] = partner.id

        # All confidence scores must meet threshold
        try:
            min_conf = float(
                self.env['ir.config_parameter']
                .sudo()
                .get_param(
                    'account_invoice_digitize_ai.ai_auto_apply_min_confidence',
                    '0.85',
                )
            )
        except (ValueError, TypeError):
            min_conf = 0.85
        conf_scores = [
            vendor_data.get('confidence') or 0.0,
            data.get('totals', {}).get('confidence') or 0.0,
            data.get('invoice', {}).get('confidence') or 0.0,
        ]
        if any(c < min_conf for c in conf_scores):
            return False

        _logger.info(
            'Auto-apply conditions met for move %s (vendor=%s, conf=%s)',
            self.id,
            partner.name,
            '/'.join('%.2f' % c for c in conf_scores),
        )
        return True

    def _ai_is_vendor_reliable(self, partner):
        """Check if vendor meets reliability criteria for auto-apply."""
        company = self.company_id or self.env.company
        score = self.env['ai.vendor.score'].search(
            [('partner_id', '=', partner.id), ('company_id', '=', company.id)],
            limit=1,
        )
        return (
            score
            and score.total_extractions >= self._AI_MIN_EXTRACTIONS_FOR_RELIABILITY
            and score.reliability_rate >= self._AI_MIN_RELIABILITY_RATE
        )
