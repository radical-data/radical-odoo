"""TTC rounding correction for AI invoice extraction.

When invoices display TTC prices (common on receipts), converting each
line to HT introduces rounding at each line.  Over N lines, this can
accumulate into a small gap between the extracted total and Odoo's
computed total.  Two strategies:
- 'adjust': nudge the highest-priced line's unit price
- 'line': add a dedicated rounding compensation line
"""

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _ai_fix_rounding_gap(self, totals):
        """Compensate TTC rounding gap using the configured strategy."""
        ICP = self.env['ir.config_parameter'].sudo()
        _p = 'account_invoice_digitize_ai.'
        if ICP.get_param(_p + 'ai_rounding_correction', 'True') != 'True':
            return
        try:
            max_tol = float(ICP.get_param(_p + 'ai_rounding_tolerance', '0.01'))
        except (ValueError, TypeError):
            max_tol = 0.01

        expected_total = totals.get('total_amount')
        if not expected_total:
            return

        digits = self.currency_id.decimal_places if self.currency_id else 2
        gap = round(expected_total - self.amount_total, digits)
        if gap == 0.0 or abs(gap) > max_tol:
            return

        strategy = ICP.get_param(_p + 'ai_rounding_strategy', 'adjust')
        if strategy == 'line':
            self._ai_fix_rounding_add_line(gap, expected_total, ICP, _p)
        else:
            self._ai_fix_rounding_adjust(gap, expected_total, digits)

    def _ai_fix_rounding_adjust(self, gap, expected_total, digits=2):
        """Strategy 'adjust': nudge the highest-priced line's unit price."""
        target = None
        for line in self.invoice_line_ids:
            if line.display_type not in (False, 'product'):
                continue
            if not target or line.price_unit > target.price_unit:
                target = line
        if not target:
            return

        old_price = target.price_unit
        target.write({'price_unit': round(old_price + gap, digits)})

        # Verify the fix actually helped
        new_gap = round(expected_total - self.amount_total, digits)
        if abs(new_gap) >= abs(gap):
            target.write({'price_unit': old_price})
        else:
            _logger.info(
                'Rounding fix: adjusted line "%s" by %+.2f for move %s',
                target.name,
                gap,
                self.id,
            )
            from markupsafe import escape
            self._ai_post_rounding_note(
                self.env._(
                    'AI Digitization applied a rounding correction of %+.2f on line "%s" '
                    'to match the extracted total.'
                ) % (gap, escape(target.name)),
            )

    def _ai_fix_rounding_add_line(self, gap, expected_total, ICP, _p):
        """Strategy 'line': add a dedicated rounding compensation line."""
        _default = 'Rounding compensation'
        label = ICP.get_param(_p + 'ai_rounding_line_label') or _default
        if label == _default:
            label = self.env._(_default)
        line_vals = {
            'name': label,
            'quantity': 1,
            'price_unit': gap,
            'tax_ids': [fields.Command.clear()],
        }
        self.write({'invoice_line_ids': [fields.Command.create(line_vals)]})

        # Verify the fix actually helped
        digits = self.currency_id.decimal_places if self.currency_id else 2
        new_gap = round(expected_total - self.amount_total, digits)
        if abs(new_gap) >= abs(gap):
            # Rollback: remove the rounding line we just added
            rounding_line = self.invoice_line_ids.filtered(
                lambda ln: ln.name == label and ln.display_type in (False, 'product')
            )
            if rounding_line:
                rounding_line[-1].unlink()
        else:
            _logger.info(
                'Rounding fix: added compensation line (%+.2f) for move %s',
                gap,
                self.id,
            )
            self._ai_post_rounding_note(
                self.env._(
                    'AI Digitization added a rounding compensation line (%+.2f) '
                    'to match the extracted total.'
                ) % gap,
            )

    def _ai_post_rounding_note(self, body):
        """Post an internal note about rounding correction on the invoice chatter."""
        try:
            self.message_post(body=body, message_type='notification', subtype_xmlid='mail.mt_note')
        except Exception:
            _logger.debug('Could not post rounding note on move %s', self.id, exc_info=True)
