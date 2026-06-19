import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .ai_preprocess_pipeline import _build_preprocess_creds

_logger = logging.getLogger(__name__)

# Average token estimates for cost display
_AVG_INPUT_TOKENS_HEADER = 2000
_AVG_OUTPUT_TOKENS_HEADER = 800
_AVG_INPUT_TOKENS_LINES = 3500
_AVG_OUTPUT_TOKENS_LINES = 1500


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_provider = fields.Selection(
        selection=[
            ('none', 'None (not configured)'),
            ('anthropic', 'Anthropic (Claude)'),
            ('openai', 'OpenAI (GPT)'),
            ('google', 'Google (Gemini)'),
            ('xai', 'xAI (Grok)'),
            ('deepseek', 'DeepSeek'),
            ('mistral', 'Mistral AI'),
        ],
        string='AI Service',
        default='anthropic',
        config_parameter='account_invoice_digitize_ai.ai_provider',
    )
    ai_api_key = fields.Char(
        string='API Key',
        config_parameter='account_invoice_digitize_ai.ai_api_key',
        groups='base.group_system',
    )
    ai_model_selection = fields.Selection(
        selection='_selection_ai_model',
        string='AI Model',
        default='claude-haiku-4-5-20251001',
        config_parameter='account_invoice_digitize_ai.ai_model_selection',
    )
    ai_extraction_mode = fields.Selection(
        selection=[
            ('guided', 'Guided (full context)'),
            ('simplified', 'Simplified (taxes only)'),
            ('free', 'Free (raw extraction)'),
        ],
        string='Extraction Mode',
        default='guided',
        config_parameter='account_invoice_digitize_ai.ai_extraction_mode',
        help='Guided: AI receives chart of accounts, taxes, and vendor history '
        'for precise account suggestions.\n'
        'Simplified: AI receives tax rates only — accountant allocates '
        'accounts manually.\n'
        'Free: AI extracts raw data with no Odoo context — no automatic matching.',
    )
    ai_extract_lines = fields.Boolean(
        string='Extract Invoice Lines',
        default=False,
        config_parameter='account_invoice_digitize_ai.ai_extract_lines',
        help='Without line extraction: one total line per invoice — the accountant '
        'allocates manually. Suited for simple invoices, uniform batches, '
        'or cost optimization.\n'
        'With line extraction: description, quantity, price and tax per line. '
        'Suited for mixed-VAT invoices, multi-account allocation, and audit trail.',
    )
    ai_show_confidence = fields.Boolean(
        string='Confidence Indicators',
        default=True,
        config_parameter='account_invoice_digitize_ai.ai_show_confidence',
        help='Each extracted field receives a reliability score (0.0 to 1.0) '
        'reflecting how confident the AI is about the value. '
        'Green = high confidence (>= 0.8), yellow = medium (>= 0.5), '
        'red = low (< 0.5). Useful to quickly spot fields that may need review.',
    )
    ai_debug_mode = fields.Boolean(
        string='Detailed Logging',
        default=False,
        config_parameter='account_invoice_digitize_ai.ai_debug_mode',
        help='Creates an extraction log for each invoice processed, containing '
        'the full prompt sent to the AI, the raw response, token counts '
        'and estimated cost. Logs are accessible from the invoice form '
        'and from Invoicing > AI Dashboard > Extraction Logs.',
    )
    ai_learning_enabled = fields.Boolean(
        string='Learning from Corrections',
        default=True,
        config_parameter='account_invoice_digitize_ai.ai_learning_enabled',
        help='When enabled, user corrections are recorded per vendor. '
        'After repeated identical corrections, the system applies them '
        'automatically on future invoices.',
    )
    ai_auto_apply_threshold = fields.Integer(
        string='Learning Threshold',
        default=3,
        config_parameter='account_invoice_digitize_ai.ai_auto_apply_threshold',
        help='Number of identical corrections needed before the system '
        'applies the correction automatically on future invoices.',
    )
    ai_async_extraction = fields.Boolean(
        string='Background Extraction',
        default=False,
        config_parameter='account_invoice_digitize_ai.ai_async_extraction',
        help='When enabled, clicking Digitize queues the invoice and returns immediately. '
        'A built-in scheduled action processes the queue every 30 seconds. '
        'The page refreshes automatically when extraction is complete.',
    )
    ai_auto_apply_enabled = fields.Boolean(
        string='Auto-apply High Confidence',
        default=False,
        config_parameter='account_invoice_digitize_ai.ai_auto_apply_enabled',
        help='Skip preview wizard when all confidence scores are high and the vendor '
        'is reliable. The invoice is filled automatically.',
    )
    ai_auto_apply_min_confidence = fields.Float(
        string='Minimum Confidence for Auto-apply',
        default=0.85,
        config_parameter='account_invoice_digitize_ai.ai_auto_apply_min_confidence',
        help='Minimum confidence score (0.0-1.0) for all fields to enable auto-apply.',
    )
    ai_email_auto_extract = fields.Boolean(
        string='Automatic Extraction',
        default=False,
        config_parameter='account_invoice_digitize_ai.ai_email_auto_extract',
        help='Automatically extract invoice data when a vendor bill arrives by email '
        '(via Odoo mail alias or incoming mail server).',
    )
    ai_extract_qr_codes = fields.Boolean(
        string='QR Code Extraction',
        default=True,
        config_parameter='account_invoice_digitize_ai.ai_extract_qr_codes',
        help='Extract payment data from Swiss QR-bill and EPC QR codes '
        'in PDF invoices. Verifies and enhances AI accuracy for IBAN, '
        'amount, and payment reference. Requires pyzbar Python library.',
    )
    ai_rounding_correction = fields.Boolean(
        string='Rounding Correction',
        default=True,
        config_parameter='account_invoice_digitize_ai.ai_rounding_correction',
        help='When the total including tax computed by Odoo differs slightly '
        'from the extracted total (due to HT rounding on each line), '
        'compensate the difference using the selected strategy.',
    )
    ai_rounding_strategy = fields.Selection(
        selection=[
            ('adjust', 'Adjust existing line'),
            ('line', 'Add a rounding line'),
        ],
        string='Rounding Strategy',
        default='adjust',
        config_parameter='account_invoice_digitize_ai.ai_rounding_strategy',
        help='How to compensate for rounding differences: adjust the unit '
        'price of the highest-value line, or add a separate compensation line.',
    )
    ai_rounding_tolerance = fields.Float(
        string='Maximum Rounding Tolerance',
        default=0.05,
        config_parameter='account_invoice_digitize_ai.ai_rounding_tolerance',
        help='Maximum TTC difference (in invoice currency) that can be '
        'corrected. Default: 0.05.',
    )
    ai_currency_symbol = fields.Char(
        related='currency_id.symbol',
    )
    ai_rounding_line_label = fields.Char(
        string='Rounding Line Label',
        default=lambda self: self.env._('Rounding compensation'),
        config_parameter='account_invoice_digitize_ai.ai_rounding_line_label',
        help='Label for the rounding compensation invoice line.',
    )
    ai_extract_customer_invoices = fields.Boolean(
        string='Enable for Customer Invoices',
        default=False,
        config_parameter='account_invoice_digitize_ai.ai_extract_customer_invoices',
        help='Enable AI extraction on customer invoices and credit notes. '
        'Uses free extraction mode (raw data, no account matching).',
    )
    ai_has_accounting = fields.Boolean(
        compute='_compute_ai_has_accounting',
    )
    ai_pyzbar_available = fields.Boolean(
        compute='_compute_ai_optional_deps',
    )
    ai_pdfplumber_available = fields.Boolean(
        compute='_compute_ai_optional_deps',
    )
    ai_facturx_available = fields.Boolean(
        compute='_compute_ai_optional_deps',
    )
    ai_cost_estimate = fields.Char(
        string='Estimated Cost per Invoice',
        compute='_compute_ai_cost_estimate',
        help='Actual cost may vary depending on the document.',
    )

    # --- Document pre-processing settings ---
    ai_preprocess_provider = fields.Selection(
        selection=[
            ('none', 'None (built-in)'),
            ('azure_di', 'Azure Document Intelligence (recommended)'),
            ('aws_textract', 'AWS Textract'),
        ],
        string='Document Recognition Service',
        default='none',
        config_parameter='account_invoice_digitize_ai.ai_preprocess_provider',
        help='Useful for non-electronic invoices (including scanned), in PDF or image format.',
    )
    ai_preprocess_mode = fields.Selection(
        selection=[
            ('ocr_replacement', 'Full recognition (AI as backup)'),
            ('claude_enrichment', 'Combined (recognition + AI cross-check)'),
            ('ocr_only', 'Text extraction only (AI analyzes the result)'),
        ],
        string='Recognition Mode',
        default='ocr_replacement',
        config_parameter='account_invoice_digitize_ai.ai_preprocess_mode',
        help='How the recognition service works with the AI.',
    )
    ai_preprocess_confidence_threshold = fields.Float(
        string='Minimum Reliability Score',
        default=0.75,
        config_parameter='account_invoice_digitize_ai.ai_preprocess_confidence_threshold',
        help='In Full recognition mode, when the recognition service returns a confidence score below this threshold (0.0-1.0), its result is discarded and the document is sent to the AI service instead.',
    )
    ai_azure_endpoint = fields.Char(
        string='Azure Endpoint',
        config_parameter='account_invoice_digitize_ai.ai_azure_endpoint',
        groups='base.group_system',
        help='Azure Document Intelligence endpoint URL (e.g. https://myresource.cognitiveservices.azure.com)',
    )
    ai_azure_api_key = fields.Char(
        string='Azure API Key',
        config_parameter='account_invoice_digitize_ai.ai_azure_api_key',
        groups='base.group_system',
    )
    ai_aws_access_key_id = fields.Char(
        string='AWS Access Key ID',
        config_parameter='account_invoice_digitize_ai.ai_aws_access_key_id',
        groups='base.group_system',
    )
    ai_aws_secret_access_key = fields.Char(
        string='AWS Secret Access Key',
        config_parameter='account_invoice_digitize_ai.ai_aws_secret_access_key',
        groups='base.group_system',
    )
    ai_aws_region = fields.Char(
        string='AWS Region',
        default='eu-west-1',
        config_parameter='account_invoice_digitize_ai.ai_aws_region',
        groups='base.group_system',
    )

    @api.constrains('ai_auto_apply_min_confidence', 'ai_preprocess_confidence_threshold')
    def _check_confidence_range(self):
        for rec in self:
            for fname in ('ai_auto_apply_min_confidence', 'ai_preprocess_confidence_threshold'):
                val = getattr(rec, fname, 0.0) or 0.0
                if not (0.0 <= val <= 1.0):
                    raise ValidationError(
                        rec.env._('"%s" must be between 0.0 and 1.0.', rec._fields[fname].string)
                    )

    @api.constrains('ai_rounding_tolerance')
    def _check_rounding_tolerance(self):
        for rec in self:
            if (rec.ai_rounding_tolerance or 0.0) < 0:
                raise ValidationError(rec.env._('Rounding tolerance cannot be negative.'))

    @api.constrains('ai_auto_apply_threshold')
    def _check_auto_apply_threshold(self):
        for rec in self:
            if (rec.ai_auto_apply_threshold or 0) < 1:
                raise ValidationError(rec.env._('Learning threshold must be at least 1.'))

    @api.model
    def _selection_ai_model(self):
        """Return models from all active providers for the dropdown.

        We return all models (not filtered by provider) so the selection list
        is always populated regardless of the saved ICP value.  The onchange
        handler pre-selects the first model of the chosen provider.
        """
        from .ai_provider import get_all_provider_models

        return get_all_provider_models()

    @api.onchange('ai_provider')
    def _onchange_ai_provider(self):
        """Reset model selection when provider changes."""
        from .ai_provider import get_provider

        provider_name = self.ai_provider
        if not provider_name or provider_name == 'none':
            self.ai_model_selection = False
            return
        try:
            provider = get_provider(provider_name)
            models = provider.get_available_models()
            if models:
                self.ai_model_selection = models[0]['id']
        except (ValueError, NotImplementedError):
            self.ai_model_selection = False

    @api.depends()
    def _compute_ai_has_accounting(self):
        installed = bool(self.env['ir.module.module'].sudo().search_count([
            ('name', '=', 'account_accountant'),
            ('state', '=', 'installed'),
        ]))
        for rec in self:
            rec.ai_has_accounting = installed

    def _compute_ai_optional_deps(self):
        from . import ai_document
        from .ai_qr_decoder import PYZBAR_AVAILABLE
        for rec in self:
            rec.ai_pyzbar_available = PYZBAR_AVAILABLE
            rec.ai_pdfplumber_available = ai_document.PDFPLUMBER_AVAILABLE
            rec.ai_facturx_available = ai_document.FACTURX_AVAILABLE

    @api.depends(
        'ai_provider',
        'ai_model_selection',
        'ai_extraction_mode',
        'ai_extract_lines',
        'ai_preprocess_provider',
    )
    def _compute_ai_cost_estimate(self):
        from .ai_provider import get_provider

        # Token reduction factors by extraction mode (less fiscal context = fewer tokens)
        _MODE_INPUT_FACTOR = {'guided': 1.0, 'simplified': 0.80, 'free': 0.65}

        for rec in self:
            try:
                prov_name = rec.ai_provider or 'anthropic'
                pp_provider = rec.ai_preprocess_provider or 'none'
                if prov_name == 'none':
                    if pp_provider != 'none':
                        pp_cost = 0.01 * 3  # ~3 pages average
                        rec.ai_cost_estimate = rec.env._(
                            '~$%.4f per invoice (recognition only)',
                            pp_cost,
                        )
                    else:
                        rec.ai_cost_estimate = ''
                    continue
                provider = get_provider(prov_name)
                model = rec.ai_model_selection or 'claude-haiku-4-5-20251001'
                mode = rec.ai_extraction_mode or 'guided'
                factor = _MODE_INPUT_FACTOR.get(mode, 1.0)
                if rec.ai_extract_lines:
                    input_t = int(_AVG_INPUT_TOKENS_LINES * factor)
                    output_t = _AVG_OUTPUT_TOKENS_LINES
                else:
                    input_t = int(_AVG_INPUT_TOKENS_HEADER * factor)
                    output_t = _AVG_OUTPUT_TOKENS_HEADER
                cost = provider.estimate_cost(input_t, output_t, model)
                # Add pre-processor cost if applicable
                if pp_provider != 'none':
                    pp_cost = 0.01 * 3  # ~3 pages average
                    rec.ai_cost_estimate = rec.env._(
                        '~$%.4f per invoice (recognition + AI)',
                        pp_cost + cost,
                    )
                else:
                    rec.ai_cost_estimate = rec.env._(
                        '~$%.4f per invoice',
                        cost,
                    )
            except Exception:
                rec.ai_cost_estimate = rec.env._('Unable to estimate (check configuration)')

    def action_ai_test_connection(self):
        """Button: test the API connection with the configured key."""
        self.ensure_one()
        from .ai_provider import get_provider

        provider_name = self.ai_provider or 'anthropic'

        api_key = self.ai_api_key
        if not api_key:
            raise UserError(self.env._('Please enter an API key before testing the connection.'))

        provider = get_provider(provider_name)
        success, message = provider.validate_api_key(api_key)

        if success:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': self.env._('Connection Successful'),
                    'message': self.env._('Your API key is valid and the connection is working.'),
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(self.env._('Connection failed: %s', message))

    def action_ai_test_preprocess_connection(self):
        """Button: test the pre-processor connection with configured credentials."""
        self.ensure_one()
        from .ai_preprocessor import get_preprocessor

        provider_name = self.ai_preprocess_provider or 'none'
        if provider_name == 'none':
            raise UserError(self.env._('Please select a recognition service before testing.'))

        preprocessor = get_preprocessor(provider_name)
        credentials = self._ai_build_preprocess_credentials(provider_name)
        if not credentials:
            raise UserError(self.env._('Please fill in the credentials before testing the connection.'))

        success, message = preprocessor.validate_credentials(credentials)
        if success:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': self.env._('Recognition Service Connection Successful'),
                    'message': self.env._('Your credentials are valid and the service is reachable.'),
                    'type': 'success',
                    'sticky': False,
                },
            }
        raise UserError(self.env._('Recognition service connection failed: %s', message))

    def action_ai_test_extraction(self):
        """Button: open the test extraction wizard."""
        self.ensure_one()
        wizard = self.env['ai.test.wizard'].create({})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.test.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _ai_build_preprocess_credentials(self, provider_name):
        """Build credentials dict from current settings values."""
        field_map = {
            'azure_endpoint': self.ai_azure_endpoint,
            'azure_api_key': self.ai_azure_api_key,
            'aws_access_key_id': self.ai_aws_access_key_id,
            'aws_secret_access_key': self.ai_aws_secret_access_key,
            'aws_region': self.ai_aws_region,
        }
        return _build_preprocess_creds(
            provider_name,
            lambda suffix, default='': field_map.get(suffix, default) or default,
        )
