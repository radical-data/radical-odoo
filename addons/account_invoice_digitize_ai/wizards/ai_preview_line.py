"""Preview line items for the extraction preview wizard.

Read-only TransientModel that displays extracted line items
in the preview wizard before the user applies the extraction.
"""

from odoo import fields, models


class AiPreviewLine(models.TransientModel):
    _name = 'ai.preview.line'
    _description = 'AI Extraction Preview Line'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'ai.preview.wizard',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(default=10)
    description = fields.Char(readonly=True)
    product_code = fields.Char(string='Product Code', readonly=True)
    quantity = fields.Float(digits=(12, 2), readonly=True)
    unit_price = fields.Float(string='Unit Price', digits=(12, 4), readonly=True)
    tax_rate = fields.Float(string='Tax %', digits=(6, 2), readonly=True)
    subtotal = fields.Float(digits=(12, 2), readonly=True)
