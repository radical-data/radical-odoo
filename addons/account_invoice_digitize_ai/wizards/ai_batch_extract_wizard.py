import logging
import math

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AiBatchExtractWizard(models.TransientModel):
    _name = 'ai.batch.extract.wizard'
    _description = 'Batch AI Extraction'

    move_ids = fields.Many2many('account.move', string='Invoices')
    move_count = fields.Integer(compute='_compute_counts')
    ready_count = fields.Integer(compute='_compute_counts')
    skip_count = fields.Integer(compute='_compute_counts')
    estimated_time = fields.Char(compute='_compute_counts', string='Estimated time')
    result_message = fields.Text(readonly=True)

    @api.depends('move_ids')
    def _compute_counts(self):
        for rec in self:
            moves = rec.move_ids
            rec.move_count = len(moves)
            ready = moves.filtered(lambda m: m.state == 'draft' and m.move_type in ('in_invoice', 'in_refund'))
            rec.ready_count = len(ready)
            rec.skip_count = rec.move_count - rec.ready_count
            # ~8 seconds per invoice (API call + processing)
            minutes = max(1, math.ceil(rec.ready_count * 8 / 60))
            rec.estimated_time = self.env._('~%d minute(s)') % minutes

    def action_extract(self):
        """Run AI extraction on all ready invoices.

        Each successful extraction is committed individually to save
        progress and avoid losing work on timeout or error.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = ICP.get_param('account_invoice_digitize_ai.ai_api_key')
        if not api_key:
            self.result_message = self.env._('No API key configured. Please go to Settings > AI Digitization.')
            return self._reopen()

        ok_count = 0
        fail_count = 0
        skip_count = 0

        ready_moves = self.move_ids.filtered(
            lambda m: m.state == 'draft' and m.move_type in ('in_invoice', 'in_refund')
        )

        for move in ready_moves:
            attachment = move._ai_get_invoice_attachment()
            if not attachment:
                skip_count += 1
                continue
            try:
                move.ai_extraction_status = 'processing'
                move._ai_trigger_extraction(api_key, attachment)
                if move.ai_extraction_status == 'done':
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception:
                _logger.warning('Batch extraction failed for move %s', move.id, exc_info=True)
                move.ai_extraction_status = 'failed'
                fail_count += 1

            # Commit after each invoice to save progress
            # pylint: disable=invalid-commit
            try:
                self.env.cr.commit()
            except AssertionError:
                pass  # In test mode, commit is blocked

        skipped_total = skip_count + (self.move_count - len(ready_moves))
        self.result_message = self.env._('%d extracted, %d failed, %d skipped.') % (ok_count, fail_count, skipped_total)
        return self._reopen()

    def _reopen(self):
        """Return action to reload the wizard with results."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
