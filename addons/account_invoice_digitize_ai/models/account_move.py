import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    ai_extraction_status = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('done', 'Done'),
            ('failed', 'Failed'),
            ('no_api', 'No API Key'),
        ],
        string='AI Extraction Status',
        copy=False,
    )
    ai_confidence = fields.Text(
        string='AI Confidence Scores',
        copy=False,
        help='JSON object with per-field confidence scores (0.0-1.0).',
    )
    ai_show_confidence = fields.Boolean(
        compute='_compute_ai_show_confidence',
    )
    ai_extraction_log_id = fields.Many2one(
        'ai.extraction.log',
        string='Extraction Log',
        copy=False,
    )
    ai_extracted_values = fields.Text(
        string='AI Extracted Values',
        copy=False,
        help='JSON snapshot of AI-filled values, used to detect user corrections.',
    )
    ai_duplicate_warning = fields.Char(
        string='Duplicate Warning',
        compute='_compute_ai_warnings',
    )
    ai_anomaly_warning = fields.Char(
        string='Anomaly Warning',
        compute='_compute_ai_warnings',
    )
    ai_proforma_warning = fields.Char(
        string='Pro-forma Warning',
        compute='_compute_ai_warnings',
    )
    ai_paid_warning = fields.Char(
        string='Paid Warning',
        compute='_compute_ai_warnings',
    )
    ai_buyer_warning = fields.Char(
        string='Buyer Warning',
        compute='_compute_ai_warnings',
    )
    ai_po_warning = fields.Char(
        string='PO Warning',
        compute='_compute_ai_warnings',
    )
    ai_tax_warning = fields.Char(
        string='Tax Warning',
        compute='_compute_ai_warnings',
    )
    ai_reverse_charge_warning = fields.Char(
        string='Reverse Charge Warning',
        compute='_compute_ai_warnings',
    )
    ai_extraction_summary = fields.Text(
        string='Extraction Summary',
        compute='_compute_ai_extraction_summary',
    )

    ai_show_extract_button = fields.Boolean(
        compute='_compute_ai_show_extract_button',
    )
    ai_last_extraction_data = fields.Text(
        string='Cached Extraction Data',
        copy=False,
        help='JSON cache of last AI extraction result. Invalidated on attachment change.',
    )
    ai_last_extraction_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Cached Attachment',
        copy=False,
        ondelete='set null',
        help='Attachment used for the cached extraction.',
    )
    ai_extraction_queued_at = fields.Datetime(
        string='Queued At',
        copy=False,
        help='Timestamp when extraction was queued for background processing.',
    )

    _AI_TRACKED_FIELDS = {
        'partner_id',
        'ref',
        'invoice_date',
        'invoice_date_due',
    }

    def _ai_get_bool_param(self, key, default='False'):
        """Read a boolean ICP parameter consistently."""
        val = (
            self.env['ir.config_parameter']
            .sudo()
            .get_param(
                'account_invoice_digitize_ai.' + key,
                default,
            )
        )
        return val not in ('False', '0', '', False)

    @staticmethod
    def _ai_notify(title, message, notif_type='warning', sticky=False):
        """Build a display_notification action dict."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notif_type,
                'sticky': sticky,
            },
        }

    def _compute_ai_show_confidence(self):
        val = self._ai_get_bool_param('ai_show_confidence', 'True')
        for rec in self:
            rec.ai_show_confidence = val

    def _compute_ai_show_extract_button(self):
        customer_enabled = self._ai_get_bool_param('ai_extract_customer_invoices')
        for rec in self:
            if rec.move_type in ('in_invoice', 'in_refund'):
                rec.ai_show_extract_button = True
            elif rec.move_type in ('out_invoice', 'out_refund'):
                rec.ai_show_extract_button = customer_enabled
            else:
                rec.ai_show_extract_button = False

    def _ai_is_customer_invoice(self):
        """Return True if this move is a customer invoice or credit note."""
        return self.move_type in ('out_invoice', 'out_refund')

    def _compute_ai_warnings(self):
        _warning_keys = (
            ('duplicate_warning', 'ai_duplicate_warning'),
            ('anomaly_warning', 'ai_anomaly_warning'),
            ('proforma_warning', 'ai_proforma_warning'),
            ('paid_warning', 'ai_paid_warning'),
            ('buyer_warning', 'ai_buyer_warning'),
            ('po_warning', 'ai_po_warning'),
            ('tax_warning', 'ai_tax_warning'),
            ('reverse_charge_warning', 'ai_reverse_charge_warning'),
        )
        for rec in self:
            messages = {field: '' for _, field in _warning_keys}
            if rec.ai_confidence:
                try:
                    conf = json.loads(rec.ai_confidence)
                    for key, field in _warning_keys:
                        w = conf.get(key, {})
                        if w.get('found'):
                            messages[field] = w.get('message', '')
                except (json.JSONDecodeError, TypeError):
                    _logger.debug('Failed to parse ai_confidence JSON for move %s.', rec.id)
            for _key, field in _warning_keys:
                setattr(rec, field, messages[field])

    _AI_SUMMARY_LABELS = {
        'partner_id': 'vendor',
        'ref': 'reference',
        'invoice_date': 'date',
        'invoice_date_due': 'due date',
        'totals': 'total',
        'currency_id': 'currency',
        'invoice_payment_term_id': 'payment terms',
        'narration': 'notes',
        'payment_reference': 'payment ref',
        'purchase_order': 'purchase order',
    }
    _AI_SUMMARY_REQUIRED = {'partner_id', 'ref', 'invoice_date', 'totals'}

    @api.depends('ai_confidence', 'ai_extraction_status')
    def _compute_ai_extraction_summary(self):
        """Build a human-readable summary of what was extracted."""
        for rec in self:
            if rec.ai_extraction_status == 'failed':
                rec.ai_extraction_summary = self.env._(
                    'Extraction failed. Please try again or use a different document.'
                )
                continue
            if not rec.ai_confidence or rec.ai_extraction_status not in ('done', 'processing'):
                rec.ai_extraction_summary = ''
                continue
            try:
                conf = json.loads(rec.ai_confidence)
            except (json.JSONDecodeError, TypeError):
                rec.ai_extraction_summary = ''
                continue
            rec.ai_extraction_summary = self._ai_build_summary_text(conf)

    def _ai_build_summary_text(self, conf):
        """Build summary text from confidence JSON dict."""
        _ = self.env._
        extracted = []
        missing = []
        for field_key, label in self._AI_SUMMARY_LABELS.items():
            score = conf.get(field_key)
            if isinstance(score, (int, float)) and score > 0:
                extracted.append(label)
            elif field_key in self._AI_SUMMARY_REQUIRED:
                missing.append(label)

        if conf.get('lines_count', 0) > 0:
            extracted.append(_('lines'))

        parts = []
        if extracted:
            parts.append(_('Extracted: %s (%d fields)') % (', '.join(extracted), len(extracted)))
        if missing:
            parts.append(_('Missing: %s') % ', '.join(missing))
        return '. '.join(parts) if parts else ''

    # ===================================================================
    # Email integration
    # ===================================================================

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Create vendor bill from incoming email.

        Called by mail.alias routing when an email arrives at the
        configured invoice alias (e.g., vendor-invoices@company.com).
        """
        if custom_values is None:
            custom_values = {}

        custom_values.setdefault('move_type', 'in_invoice')
        self._ai_message_new_defaults(msg_dict, custom_values)

        # Save explicitly provided partner_id (Odoo 19 may override it)
        explicit_partner_id = custom_values.get('partner_id')

        record = super().message_new(msg_dict, custom_values=custom_values)

        # Re-apply explicit partner_id if Odoo 19's message_new overrode it
        if explicit_partner_id and record.partner_id.id != explicit_partner_id:
            record.partner_id = explicit_partner_id

        record._ai_message_new_auto_extract()
        return record

    @api.model
    def _ai_message_new_defaults(self, msg_dict, custom_values):
        """Set journal and partner defaults for email-created bills."""
        # Odoo 19: ensure journal_id is provided (NOT NULL constraint)
        if 'journal_id' not in custom_values:
            journal = self.env['account.journal'].search(
                [('type', '=', 'purchase'), ('company_id', '=', self.env.company.id)],
                limit=1,
            )
            if journal:
                custom_values['journal_id'] = journal.id

        # Pre-identify vendor from sender email
        # Odoo 19 uses 'from', older versions use 'email_from'
        email_from = msg_dict.get('email_from', '') or msg_dict.get('from', '')
        if email_from and 'partner_id' not in custom_values:
            partner = self.env['res.partner'].search(
                [('email', '=ilike', email_from)],
                limit=1,
            )
            if partner:
                custom_values['partner_id'] = partner.id

    def _ai_message_new_auto_extract(self):
        """Auto-extract invoice data if enabled and attachment found."""
        if not self._ai_get_bool_param('ai_email_auto_extract'):
            return
        api_key = (
            self.env['ir.config_parameter']
            .sudo()
            .get_param(
                'account_invoice_digitize_ai.ai_api_key',
            )
        )
        if not api_key:
            return
        attachment = self._ai_get_invoice_attachment()
        if not attachment:
            return
        try:
            self._ai_trigger_extraction(api_key, attachment)
        except Exception:
            self.ai_extraction_status = 'failed'
            _logger.warning(
                'Auto-extraction failed for email-created bill %s',
                self.id,
                exc_info=True,
            )

    # ===================================================================
    # Button action
    # ===================================================================

    _AI_MAX_CONCURRENT_EXTRACTIONS = 5

    def _ai_check_rate_limit(self):
        """Check rate limits before extraction.

        Returns a notification action dict if blocked, or ``None`` to proceed.
        """
        if self.ai_extraction_status == 'processing':
            return self._ai_notify(
                self.env._('Extraction in Progress'),
                self.env._('An extraction is already running for this invoice.'),
            )
        company = self.company_id or self.env.company
        processing_count = self.search_count(
            [
                ('ai_extraction_status', '=', 'processing'),
                ('company_id', '=', company.id),
            ]
        )
        if processing_count >= self._AI_MAX_CONCURRENT_EXTRACTIONS:
            return self._ai_notify(
                self.env._('Too Many Extractions'),
                self.env._(
                    'Please wait — %d extractions are already running.',
                    processing_count,
                ),
            )
        return None

    def _ai_check_prerequisites(self):
        """Validate prerequisites for AI extraction.

        Returns:
            Tuple (api_key, attachment) on success.
            Returns (None, action_dict) if a prerequisite is missing.
        """
        blocked = self._ai_check_rate_limit()
        if blocked:
            return None, blocked

        ICP = self.env['ir.config_parameter'].sudo()
        api_key = ICP.get_param('account_invoice_digitize_ai.ai_api_key')
        if not api_key:
            self.ai_extraction_status = 'no_api'
            return None, self._ai_notify(
                self.env._('No API Key'),
                self.env._('Please configure your AI API key in Invoicing > Configuration > Settings.'),
                sticky=True,
            )

        attachment = self._ai_get_invoice_attachment()
        if not attachment:
            return None, self._ai_notify(
                self.env._('No Attachment'),
                self.env._('Please attach a PDF or image to this invoice.'),
            )

        return api_key, attachment

    def action_ai_extract(self):
        """Button: Digitize with AI."""
        self.ensure_one()

        api_key, attachment = self._ai_check_prerequisites()
        if api_key is None:
            return attachment  # attachment is the error action dict

        # --- Cache check: reuse previous extraction if same attachment ------
        if self.ai_last_extraction_data and self.ai_last_extraction_attachment_id == attachment:
            _logger.info('Using cached extraction data for move %s', self.id)
            try:
                data = json.loads(self.ai_last_extraction_data)
            except (json.JSONDecodeError, TypeError):
                _logger.warning('Cached extraction data is invalid JSON for move %s', self.id)
                self.ai_last_extraction_data = False
            else:
                return self._ai_open_preview_wizard(data)

        # Invalidate cache if attachment changed
        if self.ai_last_extraction_attachment_id and self.ai_last_extraction_attachment_id != attachment:
            self.ai_last_extraction_data = False
            self.ai_last_extraction_attachment_id = False

        # --- Async mode: queue for background processing -------------------
        if self._ai_get_bool_param('ai_async_extraction'):
            self.ai_extraction_status = 'processing'
            self.ai_extraction_queued_at = fields.Datetime.now()
            return self._ai_notify(
                self.env._('Extraction Queued'),
                self.env._('Extraction will be processed in the background. Refresh the page in a few seconds.'),
                notif_type='info',
            )

        # --- Run extraction in preview mode (synchronous) ------------------
        return self._ai_extract_sync(api_key, attachment)

    def _ai_extract_sync(self, api_key, attachment):
        """Run extraction synchronously and open the preview wizard."""
        self.ai_extraction_status = 'processing'
        try:
            data = self._ai_trigger_extraction(api_key, attachment, preview=True)
        except Exception:
            _logger.exception('AI extraction failed for move %s', self.id)
            self.ai_extraction_status = 'failed'
            return self._ai_notify(
                self.env._('Extraction Failed'),
                self.env._('An unexpected error occurred. Please try again or contact support.'),
                notif_type='danger',
                sticky=True,
            )

        if data is None:
            # Safety net: ensure status is not stuck at 'processing'
            if self.ai_extraction_status == 'processing':
                self.ai_extraction_status = 'failed'
            return False

        # Store in cache
        self.ai_last_extraction_data = json.dumps(data)
        self.ai_last_extraction_attachment_id = attachment.id

        # Auto-apply if conditions are met
        if self._ai_can_auto_apply(data):
            self._ai_apply_extraction(data)
            self.ai_extraction_status = 'done'
            return self._ai_notify(
                self.env._('Auto-applied'),
                self.env._('High-confidence extraction applied automatically. Please review.'),
                notif_type='success',
            )

        return self._ai_open_preview_wizard(data)

    def _ai_open_preview_wizard(self, data):
        """Open the extraction preview wizard with the given data."""
        line_commands = [
            fields.Command.create(
                {
                    'sequence': (i + 1) * 10,
                    'description': line.get('description', ''),
                    'product_code': line.get('product_code') or '',
                    'quantity': line.get('quantity') or 0,
                    'unit_price': line.get('unit_price') or 0,
                    'tax_rate': line.get('tax_rate') or 0,
                    'subtotal': line.get('subtotal_untaxed') or 0,
                }
            )
            for i, line in enumerate(data.get('lines', []))
            if line.get('description')
        ]
        wizard = self.env['ai.preview.wizard'].create(
            {
                'move_id': self.id,
                'preview_data': json.dumps(data),
                'preview_line_ids': line_commands,
            }
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.preview.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'name': self.env._('Extraction Preview'),
        }

    def action_ai_re_extract(self):
        """Button: force re-extraction (clear cache and extract again)."""
        self.ensure_one()
        self.ai_last_extraction_data = False
        self.ai_last_extraction_attachment_id = False
        return self.action_ai_extract()

    def action_ai_view_results(self):
        """Button: open preview wizard from cached extraction data."""
        self.ensure_one()
        if not self.ai_last_extraction_data:
            return False
        try:
            data = json.loads(self.ai_last_extraction_data)
        except (json.JSONDecodeError, TypeError):
            return False
        return self._ai_open_preview_wizard(data)
