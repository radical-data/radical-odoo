import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AiPreviewWizard(models.TransientModel):
    _name = 'ai.preview.wizard'
    _description = 'AI Extraction Preview'

    move_id = fields.Many2one('account.move', string='Invoice', required=True, ondelete='cascade')
    preview_data = fields.Text(string='Extraction Data (JSON)')

    # Human-readable computed fields
    vendor_name = fields.Char(compute='_compute_preview_fields')
    invoice_ref = fields.Char(compute='_compute_preview_fields')
    invoice_date = fields.Char(compute='_compute_preview_fields')
    total_amount = fields.Char(compute='_compute_preview_fields')
    currency = fields.Char(compute='_compute_preview_fields')
    line_count = fields.Integer(compute='_compute_preview_fields')
    tax_summary = fields.Char(compute='_compute_preview_fields')
    purchase_order_ref = fields.Char(compute='_compute_preview_fields')
    warnings = fields.Text(compute='_compute_preview_fields')

    # Vendor resolution fields
    vendor_match_found = fields.Boolean(compute='_compute_preview_fields')
    vendor_extracted_name = fields.Char(compute='_compute_preview_fields')
    vendor_extracted_vat = fields.Char(compute='_compute_preview_fields')
    selected_partner_id = fields.Many2one(
        'res.partner',
        string='Select Existing Vendor',
        domain="[('is_company', '=', True), ('active', '=', True)]",
    )
    create_new_vendor = fields.Boolean(string='Create New Vendor')
    new_vendor_name = fields.Char(string='Vendor Name')
    new_vendor_vat = fields.Char(string='VAT Number')

    # Extracted line items (populated at creation from preview_data JSON)
    preview_line_ids = fields.One2many(
        'ai.preview.line',
        'wizard_id',
        string='Extracted Lines',
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Pre-fill new vendor fields from extraction data."""
        records = super().create(vals_list)
        for rec in records:
            if not rec.preview_data:
                continue
            try:
                data = json.loads(rec.preview_data)
            except (json.JSONDecodeError, TypeError):
                continue
            vendor = data.get('vendor', {})
            if vendor.get('name') and not rec.new_vendor_name:
                rec.new_vendor_name = vendor['name']
            if vendor.get('vat') and not rec.new_vendor_vat:
                rec.new_vendor_vat = vendor['vat']
        return records

    @api.depends('preview_data')
    def _compute_preview_fields(self):
        from ..models import ai_matcher

        for rec in self:
            data = {}
            if rec.preview_data:
                try:
                    data = json.loads(rec.preview_data)
                except (json.JSONDecodeError, TypeError):
                    pass

            vendor = data.get('vendor', {})
            invoice = data.get('invoice', {})
            totals = data.get('totals', {})
            lines = data.get('lines', [])

            rec.vendor_name = vendor.get('name', '')
            rec.invoice_ref = invoice.get('reference', '')
            rec.purchase_order_ref = invoice.get('purchase_order_ref', '')
            rec.invoice_date = invoice.get('invoice_date', '')
            rec.currency = invoice.get('currency', '')
            rec.line_count = len(lines) if lines else 0

            # Vendor match check
            rec.vendor_extracted_name = vendor.get('name', '')
            rec.vendor_extracted_vat = vendor.get('vat', '')
            if vendor:
                partner = ai_matcher.match_partner(rec.env, vendor)
                rec.vendor_match_found = bool(partner)
            else:
                rec.vendor_match_found = False

            # Format total amount
            total = totals.get('total_amount')
            rec.total_amount = '%.2f' % total if total else ''

            # Tax summary
            tax_lines = data.get('tax_lines', [])
            if tax_lines:
                parts = []
                for tl in tax_lines:
                    rate = tl.get('tax_rate', 0)
                    amount = tl.get('tax_amount', 0)
                    parts.append('%.1f%%: %.2f' % (rate, amount))
                rec.tax_summary = ' | '.join(parts)
            else:
                rec.tax_summary = ''

            # Warnings
            _ = self.env._
            warning_parts = []
            doc_type = data.get('document_type', '')
            if doc_type == 'proforma':
                warning_parts.append(_('This document appears to be a pro-forma invoice.'))
            if doc_type == 'credit_note':
                warning_parts.append(_('This document is a credit note.'))
            if data.get('is_marked_paid'):
                warning_parts.append(_('This document appears to be marked as already paid.'))
            rec.warnings = '\n'.join(warning_parts) if warning_parts else ''

    def action_apply(self):
        """Apply the previewed extraction data to the invoice."""
        self.ensure_one()
        if not self.preview_data:
            return {'type': 'ir.actions.act_window_close'}

        try:
            data = json.loads(self.preview_data)
        except (json.JSONDecodeError, TypeError):
            return {'type': 'ir.actions.act_window_close'}

        # Vendor resolution: user-selected or newly created partner
        vendor_name = (self.new_vendor_name or '').strip()
        if self.create_new_vendor and vendor_name:
            partner_vals = {
                'name': vendor_name,
                'is_company': True,
                'supplier_rank': 1,
            }
            if self.new_vendor_vat:
                partner_vals['vat'] = self.new_vendor_vat.strip()
            partner = self.env['res.partner'].create(partner_vals)
            data['_force_partner_id'] = partner.id
        elif self.selected_partner_id:
            data['_force_partner_id'] = self.selected_partner_id.id

        move = self.move_id
        move._ai_apply_extraction(data)
        move.ai_extraction_status = 'done'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._('Extraction Applied'),
                'message': self.env._('Invoice fields have been filled. Please review the results.'),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_discard(self):
        """Discard the extraction and close the wizard."""
        self.ensure_one()
        self.move_id.ai_extraction_status = 'pending'
        return {'type': 'ir.actions.act_window_close'}
