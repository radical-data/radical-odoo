import base64
import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Minimal sample invoice text for testing the full extraction pipeline
SAMPLE_INVOICE_TEXT = """\
ACME Services SARL
12 rue de la Paix, 75001 Paris
TVA: FR12345678901

FACTURE N° TEST-2024-001
Date: 15/01/2024
Échéance: 15/02/2024

Client:
Ma Société SAS
10 avenue des Champs-Élysées, 75008 Paris

Désignation                    Qté    P.U. HT    Total HT
Prestation de conseil           10     80,00       800,00
Frais de déplacement             1    200,00       200,00

Total HT:     1 000,00 EUR
TVA 20%:        200,00 EUR
Total TTC:    1 200,00 EUR

Conditions de paiement: 30 jours
IBAN: FR76 3000 6000 0112 3456 7890 189
"""

_MIME_MAP = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.tiff': 'image/tiff',
    '.tif': 'image/tiff',
    '.bmp': 'image/bmp',
}


class AiTestWizard(models.TransientModel):
    _name = 'ai.test.wizard'
    _description = 'Test AI Extraction Pipeline'

    test_mode = fields.Selection(
        [
            ('full_pipeline', 'Full Pipeline'),
            ('text_extraction', 'Text Extraction'),
            ('preprocessing', 'Document Recognition'),
            ('prompt_preview', 'Prompt Preview'),
        ],
        string='Test Mode',
        default='full_pipeline',
        required=True,
    )
    test_document = fields.Binary(string='Test Document')
    test_document_name = fields.Char(string='Document Filename')
    use_sample = fields.Boolean(string='Use sample invoice', default=True)

    result_status = fields.Selection(
        [('pending', 'Pending'), ('success', 'Success'), ('failed', 'Failed')],
        default='pending',
        readonly=True,
    )
    result_message = fields.Text(readonly=True)
    result_details = fields.Text(readonly=True)

    def action_run_test(self):
        """Route to the correct test method based on test_mode."""
        self.ensure_one()
        handler = {
            'full_pipeline': self._test_full_pipeline,
            'text_extraction': self._test_text_extraction,
            'preprocessing': self._test_preprocessing,
            'prompt_preview': self._test_prompt_preview,
        }.get(self.test_mode, self._test_full_pipeline)
        try:
            handler()
        except UserError:
            raise
        except Exception as exc:
            _logger.exception('Test wizard failed (mode=%s)', self.test_mode)
            self.result_status = 'failed'
            self.result_message = str(exc)
        return self._reopen()

    # ------------------------------------------------------------------
    # Mode 1: Full Pipeline
    # ------------------------------------------------------------------

    def _test_full_pipeline(self):
        """Run a full extraction test with sample or uploaded document."""
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = ICP.get_param('account_invoice_digitize_ai.ai_api_key', '')
        if not api_key:
            self.result_status = 'failed'
            self.result_message = self.env._('No API key configured.')
            return

        provider_name = ICP.get_param('account_invoice_digitize_ai.ai_provider', 'anthropic')
        model_id = ICP.get_param('account_invoice_digitize_ai.ai_model_selection', 'claude-haiku-4-5-20251001')

        from ..models.ai_provider import get_provider

        provider = get_provider(provider_name)

        if self.use_sample or not self.test_document:
            self._test_full_pipeline_sample(provider, api_key, model_id)
        else:
            self._test_full_pipeline_upload(provider, api_key, model_id)

    def _test_full_pipeline_sample(self, provider, api_key, model_id):
        """Full pipeline test with the built-in sample invoice."""
        from ..models.ai_prompt import EXTRACTION_SCHEMA_NO_LINES, SYSTEM_PROMPT

        user_content = [{'type': 'text', 'text': EXTRACTION_SCHEMA_NO_LINES + '\n\n' + SAMPLE_INVOICE_TEXT}]
        result = provider.extract(api_key, SYSTEM_PROMPT, user_content, model_id)

        if not result.get('success'):
            self.result_status = 'failed'
            self.result_message = self.env._(
                'API call failed: %s',
                result.get('message', 'Unknown error'),
            )
            return

        data = result.get('data')
        if not data:
            self.result_status = 'failed'
            self.result_message = self.env._('API returned no parseable data.')
            self.result_details = result.get('raw_text', '')[:2000]
            return

        checks = self._validate_test_response(data, result)
        self._apply_checks(checks)

    def _test_full_pipeline_upload(self, provider, api_key, model_id):
        """Full pipeline test with an uploaded document."""
        raw_data, mimetype = self._get_uploaded_document()
        ICP = self.env['ir.config_parameter'].sudo()
        extract_lines = ICP.get_param('account_invoice_digitize_ai.ai_extract_lines', 'False') == 'True'

        move = self.env['account.move'].new({'move_type': 'in_invoice'})
        doc_info = move._ai_prepare_document(raw_data, mimetype, extract_lines)

        if doc_info.get('unsupported'):
            self.result_status = 'failed'
            self.result_message = self.env._('Unsupported file type: %s', mimetype)
            return

        vendor = move._ai_pre_identify_vendor(doc_info.get('text', ''))
        company = self.env.company
        system_prompt, user_content, _ = move._ai_build_content(
            doc_info, raw_data, mimetype, vendor, company, extract_lines
        )

        result = provider.extract(api_key, system_prompt, user_content, model_id)
        if not result.get('success'):
            self.result_status = 'failed'
            self.result_message = self.env._(
                'API call failed: %s',
                result.get('message', 'Unknown error'),
            )
            return

        data = result.get('data')
        if not data:
            self.result_status = 'failed'
            self.result_message = self.env._('API returned no parseable data.')
            self.result_details = result.get('raw_text', '')[:2000]
            return

        checks = self._validate_uploaded_response(data, result)
        self._apply_checks(checks)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _get_uploaded_document(self):
        """Decode the uploaded document and guess its mimetype.

        Returns (raw_data_bytes, mimetype_string).
        Raises UserError if no document is uploaded.
        """
        if not self.test_document:
            raise UserError(self.env._('Please upload a document for this test mode.'))
        raw_data = base64.b64decode(self.test_document)
        mimetype = self._guess_mimetype(self.test_document_name or '')
        return raw_data, mimetype

    @staticmethod
    def _guess_mimetype(filename):
        """Guess MIME type from filename extension."""
        ext = ('.' + filename.rsplit('.', 1)[-1]).lower() if '.' in filename else ''
        return _MIME_MAP.get(ext, 'application/pdf')

    def _validate_test_response(self, data, result):
        """Validate the test extraction response. Returns list of (bool, message)."""
        _ = self.env._
        checks = []
        # Structure checks
        checks.append((isinstance(data.get('vendor'), dict), _('vendor section present')))
        checks.append((isinstance(data.get('invoice'), dict), _('invoice section present')))
        checks.append((isinstance(data.get('totals'), dict), _('totals section present')))

        # Content checks (from the sample invoice)
        vendor = data.get('vendor', {})
        checks.append((bool(vendor.get('name')), _('vendor name extracted: %s', vendor.get('name', ''))))

        invoice = data.get('invoice', {})
        checks.append(
            (bool(invoice.get('reference')), _('invoice reference extracted: %s', invoice.get('reference', '')))
        )
        checks.append(
            (bool(invoice.get('invoice_date')), _('invoice date extracted: %s', invoice.get('invoice_date', '')))
        )

        totals = data.get('totals', {})
        total = totals.get('total_amount')
        checks.append((total is not None and abs(total - 1200.0) < 1.0, _('total amount correct: %s', total)))

        # Token usage
        tokens = result.get('input_tokens', 0) + result.get('output_tokens', 0)
        checks.append((tokens > 0, _('token usage reported: %s tokens', tokens)))

        return checks

    def _validate_uploaded_response(self, data, result):
        """Validate extraction of an uploaded document (structure checks only)."""
        _ = self.env._
        checks = []
        checks.append((isinstance(data.get('vendor'), dict), _('vendor section present')))
        checks.append((isinstance(data.get('invoice'), dict), _('invoice section present')))
        checks.append((isinstance(data.get('totals'), dict), _('totals section present')))

        vendor = data.get('vendor', {})
        checks.append((bool(vendor.get('name')), _('vendor name extracted: %s', vendor.get('name', ''))))

        invoice = data.get('invoice', {})
        checks.append(
            (bool(invoice.get('reference')), _('invoice reference extracted: %s', invoice.get('reference', '')))
        )
        checks.append(
            (bool(invoice.get('invoice_date')), _('invoice date extracted: %s', invoice.get('invoice_date', '')))
        )

        totals = data.get('totals', {})
        checks.append(
            (totals.get('total_amount') is not None, _('total amount extracted: %s', totals.get('total_amount')))
        )

        tokens = result.get('input_tokens', 0) + result.get('output_tokens', 0)
        checks.append((tokens > 0, _('token usage reported: %s tokens', tokens)))

        return checks

    def _apply_checks(self, checks):
        """Set result_status/message/details from a list of (bool, msg) checks."""
        _ = self.env._
        passed = sum(1 for ok, __ in checks if ok)
        total = len(checks)
        if passed == total:
            self.result_status = 'success'
            self.result_message = _(
                'All %s checks passed! Your configuration is working correctly.',
                total,
            )
        else:
            self.result_status = 'failed'
            self.result_message = _('%s of %s checks passed.', passed, total)
        pass_label = _('PASS')
        fail_label = _('FAIL')
        self.result_details = '\n'.join('%s %s' % (pass_label if ok else fail_label, msg) for ok, msg in checks)

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
