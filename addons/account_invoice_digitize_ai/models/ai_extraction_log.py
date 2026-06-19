from odoo import api, fields, models


class AiExtractionLog(models.Model):
    _name = 'ai.extraction.log'
    _description = 'AI Extraction Log'
    _order = 'extraction_date desc'

    move_id = fields.Many2one(
        'account.move',
        string='Invoice',
        required=True,
        ondelete='cascade',
    )
    prompt_sent = fields.Text()
    response_received = fields.Text()
    model_used = fields.Char()
    input_tokens = fields.Integer()
    output_tokens = fields.Integer()
    total_tokens = fields.Integer(
        compute='_compute_total_tokens',
        store=True,
        help='Sum of input and output tokens.',
    )
    cost_estimated = fields.Float(string='Estimated Cost', digits=(10, 6))
    extraction_date = fields.Datetime(
        default=fields.Datetime.now,
    )
    success = fields.Boolean(default=False)
    error_message = fields.Char(string='Error Message')

    # Lightweight fields (always populated, even without debug mode)
    vendor_name = fields.Char(string='Vendor Name')
    overall_confidence = fields.Float(string='Overall Confidence', digits=(3, 2))
    duration_seconds = fields.Float(string='Duration (s)', digits=(6, 2))
    provider_name = fields.Char(string='Provider')
    extraction_mode = fields.Selection(
        selection=[
            ('text', 'Text'),
            ('vision', 'Vision'),
            ('facturx', 'Factur-X'),
            ('preprocess', 'Pre-processor'),
        ],
        string='Mode',
    )

    @api.depends('input_tokens', 'output_tokens')
    def _compute_total_tokens(self):
        for rec in self:
            rec.total_tokens = (rec.input_tokens or 0) + (rec.output_tokens or 0)
