import logging

from psycopg2 import IntegrityError

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AiVendorScore(models.Model):
    _name = 'ai.vendor.score'
    _description = 'AI Vendor Extraction Score'
    _order = 'reliability_rate asc'
    _rec_name = 'partner_id'

    partner_id = fields.Many2one(
        'res.partner',
        string='Vendor',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    total_extractions = fields.Integer(
        default=0,
    )
    correct_extractions = fields.Integer(
        default=0,
        help='Extractions where no user correction was needed.',
    )
    reliability_rate = fields.Float(
        compute='_compute_reliability_rate',
        store=True,
        digits=(5, 2),
        help='Percentage of correct extractions (0-100).',
    )
    last_reliability_rate = fields.Float(
        string='Previous Rate',
        digits=(5, 2),
        help='Reliability rate before the last update, used for degradation detection.',
    )
    last_computed_date = fields.Datetime(
        string='Last Updated',
    )

    _unique_partner_company = models.UniqueIndex('(partner_id, company_id)')

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    @api.depends('total_extractions', 'correct_extractions')
    def _compute_reliability_rate(self):
        for rec in self:
            if rec.total_extractions:
                rec.reliability_rate = round(rec.correct_extractions / rec.total_extractions * 100, 2)
            else:
                rec.reliability_rate = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def update_score(env, partner, had_corrections, company=None):
        """Update the extraction score for a vendor.

        Called after an extraction is applied. Increments counters and
        checks for reliability degradation.

        Args:
            env: Odoo environment.
            partner: res.partner recordset (single).
            had_corrections: bool — True if user corrected any field.
            company: res.company recordset (single), defaults to env.company.
        """
        if not company:
            company = env.company
        Score = env['ai.vendor.score']
        score = Score.search([('partner_id', '=', partner.id), ('company_id', '=', company.id)], limit=1)

        if not score:
            try:
                with env.cr.savepoint():
                    score = Score.create(
                        {
                            'partner_id': partner.id,
                            'company_id': company.id,
                            'total_extractions': 0,
                            'correct_extractions': 0,
                        }
                    )
            except IntegrityError:
                # Race condition: another request created the same record
                score = Score.search(
                    [('partner_id', '=', partner.id), ('company_id', '=', company.id)],
                    limit=1,
                )
                if not score:
                    return

        # Save previous rate before updating
        old_rate = score.reliability_rate

        vals = {
            'total_extractions': score.total_extractions + 1,
            'last_reliability_rate': old_rate,
            'last_computed_date': fields.Datetime.now(),
        }
        if not had_corrections:
            vals['correct_extractions'] = score.correct_extractions + 1

        score.write(vals)

        # Check for degradation
        AiVendorScore._check_degradation(env, partner, score)

    @staticmethod
    def _check_degradation(env, partner, score):
        """Warn if reliability has dropped significantly.

        Triggers a log warning if the rate dropped by more than 20
        percentage points compared to the previous rate, and the vendor
        has at least 5 extractions (to avoid false alarms on small samples).
        """
        if score.total_extractions < 5:
            return

        drop = score.last_reliability_rate - score.reliability_rate
        if drop > 20:
            _logger.warning(
                'Vendor reliability degradation: %s — rate dropped from %.0f%% to %.0f%% (Δ%.0f%%)',
                partner.name,
                score.last_reliability_rate,
                score.reliability_rate,
                drop,
            )
