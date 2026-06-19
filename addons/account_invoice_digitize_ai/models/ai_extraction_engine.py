import base64
import json
import logging
import time as time_mod

from odoo import models

from . import ai_document
from . import ai_validator

_logger = logging.getLogger(__name__)

_AI_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
_VALID_PROVIDERS = {'anthropic', 'openai', 'google', 'xai', 'deepseek', 'mistral', 'local'}
_VALID_PP_PROVIDERS = {'none', 'azure_di', 'aws_textract'}


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ===================================================================
    # Configuration
    # ===================================================================

    def _ai_get_config(self):
        """Read all AI config parameters in a single batch (avoids repeated DB lookups)."""
        ICP = self.env['ir.config_parameter'].sudo()
        _p = 'account_invoice_digitize_ai.'

        provider_name = ICP.get_param(_p + 'ai_provider', 'anthropic')
        if provider_name not in _VALID_PROVIDERS:
            _logger.warning('Unknown AI provider %r, falling back to anthropic', provider_name)
            provider_name = 'anthropic'

        pp_provider = ICP.get_param(_p + 'ai_preprocess_provider', 'none')
        if pp_provider not in _VALID_PP_PROVIDERS:
            _logger.warning('Unknown preprocess provider %r, falling back to none', pp_provider)
            pp_provider = 'none'

        try:
            threshold = float(ICP.get_param(_p + 'ai_preprocess_confidence_threshold', '0.75'))
            threshold = max(0.0, min(1.0, threshold))
        except (ValueError, TypeError):
            threshold = 0.75

        return {
            'provider_name': provider_name,
            'model_id': ICP.get_param(_p + 'ai_model_selection', 'claude-haiku-4-5-20251001'),
            'extract_lines': ICP.get_param(_p + 'ai_extract_lines', 'False') == 'True',
            'debug_mode': ICP.get_param(_p + 'ai_debug_mode', 'False') == 'True',
            'preprocess_provider': pp_provider,
            'preprocess_mode': ICP.get_param(_p + 'ai_preprocess_mode', 'ocr_replacement'),
            'preprocess_threshold': threshold,
            'extraction_mode': ICP.get_param(_p + 'ai_extraction_mode', 'guided'),
            'extract_qr_codes': ICP.get_param(_p + 'ai_extract_qr_codes', 'True') == 'True',
        }

    # ===================================================================
    # Core extraction pipeline
    # ===================================================================

    @staticmethod
    def _ai_decode_attachment(attachment):
        """Decode attachment data. Returns ``(raw_data, mimetype)`` or ``(None, None)``."""
        if not attachment.datas:
            _logger.warning('Attachment %s has no data, skipping extraction', attachment.id)
            return None, None
        try:
            raw_data = base64.b64decode(attachment.datas)
        except Exception:
            _logger.warning('Failed to decode attachment %s', attachment.id)
            return None, None
        if len(raw_data) > _AI_MAX_FILE_SIZE:
            _logger.warning(
                'Attachment %s too large (%d bytes), skipping extraction',
                attachment.id,
                len(raw_data),
            )
            return None, None
        return raw_data, attachment.mimetype or ''

    def _ai_trigger_extraction(self, api_key, attachment, preview=False):
        """Main extraction pipeline (orchestrator).

        Steps: attachment → Factur-X check → pre-processing → text/vision →
               prompt → API call → parse → validate → apply → log.

        When *preview* is ``True``, the data is **not** applied to the
        invoice.  Instead the validated extraction dict is returned so
        a preview wizard can display it first.
        """
        cfg = self._ai_get_config()

        raw_data, mimetype = self._ai_decode_attachment(attachment)
        if raw_data is None:
            self.ai_extraction_status = 'failed'
            return

        data = self._ai_run_pipeline(api_key, cfg, raw_data, mimetype)
        if data is None:
            return
        if preview:
            return data
        if isinstance(data, dict) and data.get('_facturx'):
            self._ai_apply_facturx(data['_xml'])
        else:
            self._ai_apply_extraction(data, cfg=cfg)
        self.ai_extraction_status = 'done'

    def _ai_run_pipeline(self, api_key, cfg, raw_data, mimetype):
        """Execute the extraction pipeline: Factur-X → preprocess → AI.

        Returns the extraction dict, or ``None`` on failure/non-applicable.
        """
        extract_lines = cfg['extract_lines']
        debug_mode = cfg['debug_mode']
        extract_qr_codes = cfg['extract_qr_codes']

        # --- 1. Factur-X shortcut ----------------------------------------
        facturx_xml = self._ai_try_facturx(raw_data, mimetype)
        if facturx_xml is not None:
            return {'_facturx': True, '_xml': facturx_xml}

        # --- 1b-2. Pre-processing and document preparation ----------------
        pp_shortcut, doc_info, preprocess_context = self._ai_preprocess_or_prepare(
            cfg['preprocess_provider'],
            cfg['preprocess_mode'],
            cfg['preprocess_threshold'],
            raw_data,
            mimetype,
            extract_lines,
            debug_mode,
            extract_qr_codes=extract_qr_codes,
        )
        if pp_shortcut is not None:
            return pp_shortcut

        if doc_info.get('unsupported') or doc_info.get('is_proforma'):
            self._ai_handle_doc_issue(doc_info)
            return None

        # --- 3. Vendor pre-identification --------------------------------
        vendor = None
        if doc_info.get('text'):
            vendor = self._ai_pre_identify_vendor(doc_info['text'])

        # --- 4. Build prompt + content ------------------------------------
        company = self.company_id or self.env.company
        system_prompt, user_content, user_prompt = self._ai_build_content(
            doc_info,
            raw_data,
            mimetype,
            vendor,
            company,
            extract_lines,
            preprocess_context=preprocess_context,
            cfg=cfg,
        )

        # --- 4b. Prompt size estimation -----------------------------------
        estimated_tokens = self._ai_estimate_prompt_tokens(system_prompt, user_prompt)
        if estimated_tokens > 50000:
            _logger.warning(
                'Large document detected: ~%d estimated input tokens (invoice %s)',
                estimated_tokens,
                self.id,
            )

        # --- 5-8. API call, validate, retry --------------------------------
        # Attach raw document data to doc_info for vision retry
        doc_info['raw_data'] = raw_data
        doc_info['mimetype'] = mimetype
        return self._ai_call_and_validate(
            api_key,
            cfg,
            system_prompt,
            user_content,
            user_prompt,
            doc_info,
        )

    @staticmethod
    def _ai_try_facturx(raw_data, mimetype):
        """Try to extract Factur-X data from a PDF. Returns dict or ``None``."""
        if ai_document.is_pdf(mimetype) and ai_document.FACTURX_AVAILABLE:
            return ai_document.detect_facturx(raw_data) or None
        return None

    def _ai_handle_doc_issue(self, doc_info):
        """Handle unsupported or pro-forma documents by setting appropriate status."""
        if doc_info.get('unsupported'):
            self.ai_extraction_status = 'failed'
            self.ai_confidence = json.dumps({'overall': 0.0})
            return
        # Pro-forma
        self.ai_confidence = json.dumps(
            {
                'overall': 0.0,
                'proforma_warning': {
                    'found': True,
                    'message': 'This document appears to be a pro-forma/quote, not a real invoice.',
                },
            }
        )
        self.ai_extraction_status = 'done'

    def _ai_call_provider(self, api_key, cfg, system_prompt, user_content, user_prompt, mode='text'):
        """Call AI provider, log the result, and return the raw API result dict."""
        from .ai_provider import get_provider

        start_time = time_mod.time()
        provider = get_provider(cfg['provider_name'])
        result = provider.extract(api_key, system_prompt, user_content, cfg['model_id'])
        self._ai_create_log(
            user_prompt,
            result,
            provider_name=cfg['provider_name'],
            model_id=cfg['model_id'],
            debug_mode=cfg['debug_mode'],
            start_time=start_time,
            mode=mode,
        )
        return result

    def _ai_call_and_validate(self, api_key, cfg, system_prompt, user_content, user_prompt, doc_info):
        """Call AI provider, validate and optionally retry in vision mode.

        Returns validated extraction data dict, or ``None`` on API error.
        """
        mode = 'text'
        if any(isinstance(c, dict) and c.get('type') == 'image' for c in user_content):
            mode = 'vision'

        result = self._ai_call_provider(
            api_key,
            cfg,
            system_prompt,
            user_content,
            user_prompt,
            mode=mode,
        )

        data = self._ai_check_api_result(result)
        if data is None:
            return None

        # Inject QR data for cross-validation (temporary key, removed after)
        qr_data = doc_info.get('qr_data')
        if qr_data:
            data['_qr_data'] = qr_data
        failure_count = ai_validator.cross_validate(
            data,
            detected_number_format=doc_info.get('detected_number_format'),
        )
        data.pop('_qr_data', None)
        mimetype = doc_info.get('mimetype', '')
        if failure_count >= 2 and not doc_info.get('is_vision') and ai_document.is_pdf(mimetype):
            retry_data = self._ai_retry_vision(api_key, cfg, doc_info['raw_data'])
            if retry_data is not None:
                data = retry_data

        return data

    def _ai_retry_vision(self, api_key, cfg, raw_data):
        """Retry extraction in vision mode after text-mode cross-validation failures.

        Returns the new validated data dict, or ``None`` if vision retry
        also fails or is not better.
        """
        _logger.info('Retrying extraction in vision mode (text-mode had ≥2 validation failures)')

        # Build a vision-only doc_info (re-extract QR codes for cross-validation)
        doc_info = {
            'text': '',
            'is_vision': True,
            'pdf_metadata': ai_document.extract_pdf_metadata(raw_data),
            'detected_number_format': None,
            'table_markdown': '',
            'is_proforma': False,
            'unsupported': False,
            'qr_data': [],
        }
        self._ai_extract_qr_data(doc_info, raw_data, 'application/pdf')

        vendor = None
        text = ai_document.extract_text_from_pdf(raw_data)
        if text:
            vendor = self._ai_pre_identify_vendor(text)

        company = self.company_id or self.env.company
        system_prompt, user_content, user_prompt = self._ai_build_content(
            doc_info,
            raw_data,
            'application/pdf',
            vendor,
            company,
            cfg['extract_lines'],
        )

        result = self._ai_call_provider(
            api_key,
            cfg,
            system_prompt,
            user_content,
            user_prompt,
            mode='vision',
        )

        if not result.get('success') or not result.get('data'):
            _logger.warning('Vision retry did not produce usable data')
            return None

        data = result['data']
        qr_data = doc_info.get('qr_data')
        if qr_data:
            data['_qr_data'] = qr_data
        retry_failures = ai_validator.cross_validate(data)
        data.pop('_qr_data', None)
        if retry_failures >= 2:
            _logger.warning('Vision retry also had %d validation failures — keeping original', retry_failures)
            return None

        _logger.info('Vision retry passed cross-validation (%d failures)', retry_failures)
        return data

    def _ai_check_api_result(self, result):
        """Check API result for errors.

        Returns the extracted data dict, or ``None`` if the result is an
        error (also sets ``ai_extraction_status`` accordingly).
        """
        if not result.get('success'):
            error_code = result.get('error', 'unknown')
            error_msg = result.get('message', 'Unknown error')
            _logger.warning('AI extraction error (%s): %s', error_code, error_msg)
            if error_code in ('auth', 'credits', 'forbidden'):
                self.ai_extraction_status = 'no_api'
            else:
                self.ai_extraction_status = 'failed'
            return None

        data = result.get('data')
        if not data:
            _logger.warning('AI returned no parseable data: %s', result.get('parse_error', ''))
            self.ai_extraction_status = 'failed'
            return None

        return data
