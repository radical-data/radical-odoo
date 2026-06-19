"""Pre-processing pipeline methods for account.move.

Extracted from account_move.py for separation of concerns.  Handles
external OCR / document intelligence pre-processing (Azure Document
Intelligence, AWS Textract) with three modes: OCR Replacement,
Claude Enrichment, OCR Only.
"""

import json
import logging

from odoo import models

from . import ai_document
from . import ai_validator

_logger = logging.getLogger(__name__)


def _build_preprocess_creds(provider_name, get_fn):
    """Build credentials dict for a pre-processor.

    Args:
        provider_name: ``'azure_di'`` or ``'aws_textract'``.
        get_fn: Callable ``(suffix, default='') -> str`` that fetches
            a configuration value by suffix (e.g. ``'azure_endpoint'``).

    Returns:
        dict or None: Credentials dict, or ``None`` if incomplete.
    """
    if provider_name == 'azure_di':
        endpoint = get_fn('azure_endpoint', '')
        api_key = get_fn('azure_api_key', '')
        if endpoint and api_key:
            return {'endpoint': endpoint, 'api_key': api_key}
        return None
    if provider_name == 'aws_textract':
        access_key = get_fn('aws_access_key_id', '')
        secret_key = get_fn('aws_secret_access_key', '')
        region = get_fn('aws_region', 'eu-west-1')
        if access_key and secret_key:
            return {'access_key_id': access_key, 'secret_access_key': secret_key, 'region': region}
        return None
    return None


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ===================================================================
    # Pre-processing helpers
    # ===================================================================

    def _ai_preprocess_or_prepare(
        self,
        preprocess_provider,
        preprocess_mode,
        preprocess_threshold,
        raw_data,
        mimetype,
        extract_lines,
        debug_mode,
        extract_qr_codes=True,
    ):
        """Run external pre-processing and prepare document for Claude.

        Returns ``(pp_shortcut, doc_info, preprocess_context)`` where:
        - *pp_shortcut* is a validated extraction dict if OCR Replacement
          succeeded (caller should apply and return), else ``None``.
        - *doc_info* is the document info dict for prompt building.
        - *preprocess_context* is extra context for Claude Enrichment mode.
        """
        preprocess_result = None
        if preprocess_provider != 'none':
            preprocess_result = self._ai_try_preprocess(
                preprocess_provider,
                preprocess_mode,
                raw_data,
                mimetype,
                debug_mode,
            )

        # OCR Replacement: use pre-processor directly if confident enough
        if preprocess_result and preprocess_mode == 'ocr_replacement':
            pp_shortcut = self._ai_check_ocr_replacement(
                preprocess_result,
                preprocess_threshold,
            )
            if pp_shortcut is not None:
                return pp_shortcut, None, ''

        # Document preparation
        if preprocess_result and preprocess_mode == 'ocr_only' and preprocess_result.get('text'):
            doc_info = self._ai_prepare_document_with_preprocess_text(
                preprocess_result['text'],
                raw_data,
                mimetype,
                extract_lines,
                extract_qr_codes=extract_qr_codes,
            )
        else:
            doc_info = self._ai_prepare_document(raw_data, mimetype, extract_lines, extract_qr_codes=extract_qr_codes)

        # Build preprocess context for Claude Enrichment
        preprocess_context = ''
        if preprocess_result and preprocess_mode == 'claude_enrichment' and preprocess_result.get('data'):
            preprocess_context = self._ai_format_preprocess_data(preprocess_result)

        return None, doc_info, preprocess_context

    @staticmethod
    def _ai_check_ocr_replacement(preprocess_result, threshold):
        """Check if OCR Replacement result is usable. Returns data or ``None``."""
        pp_data = preprocess_result.get('data')
        pp_confidence = preprocess_result.get('confidence', 0.0)
        if pp_data and pp_confidence >= threshold:
            failure_count = ai_validator.cross_validate(pp_data)
            if failure_count < 2:
                return pp_data
            _logger.info(
                'Pre-processor result failed validation (%d failures), falling back to Claude',
                failure_count,
            )
        else:
            _logger.info(
                'Pre-processor confidence (%.2f) below threshold (%.2f), falling back to Claude',
                pp_confidence,
                threshold,
            )
        return None

    def _ai_try_preprocess(self, provider_name, mode, raw_data, mimetype, debug_mode):
        """Try external pre-processing. Returns result dict or ``None`` on failure."""
        from .ai_preprocessor import get_preprocessor

        try:
            preprocessor = get_preprocessor(provider_name)
            credentials = self._ai_get_preprocess_credentials(provider_name)
            if not credentials:
                _logger.warning('No credentials configured for pre-processor %s', provider_name)
                return None

            if mode == 'ocr_only':
                result = preprocessor.extract_text(credentials, raw_data, mimetype)
            else:
                result = preprocessor.extract_structured(credentials, raw_data, mimetype)

            if not result.get('success'):
                _logger.warning('Pre-processor %s failed: %s', provider_name, result.get('error', 'unknown'))
                return None

            if debug_mode:
                self._ai_log_preprocess(provider_name, result)

            return result
        except Exception:
            _logger.warning(
                'Pre-processor %s raised an exception, falling back to internal pipeline',
                provider_name,
                exc_info=True,
            )
            return None

    def _ai_get_preprocess_credentials(self, provider_name):
        """Build credentials dict from ir.config_parameter for the given pre-processor."""
        ICP = self.env['ir.config_parameter'].sudo()

        def _get(suffix, default=''):
            return ICP.get_param('account_invoice_digitize_ai.ai_%s' % suffix, default)

        return _build_preprocess_creds(provider_name, _get)

    def _ai_prepare_document_with_preprocess_text(
        self, preprocess_text, raw_data, mimetype, extract_lines, extract_qr_codes=True
    ):
        """Build doc_info using pre-processor OCR text instead of PyPDF2."""
        result = {
            'text': preprocess_text,
            'is_vision': False,
            'pdf_metadata': {},
            'detected_number_format': None,
            'table_markdown': '',
            'is_proforma': False,
            'unsupported': False,
            'qr_data': [],
        }
        # PDF metadata (still use PyPDF2 for metadata — it's free)
        if ai_document.is_pdf(mimetype):
            result['pdf_metadata'] = ai_document.extract_pdf_metadata(raw_data)
        # Number format detection on pre-processor text
        result['detected_number_format'] = ai_document.detect_number_format(preprocess_text)
        # QR code extraction
        if extract_qr_codes:
            self._ai_extract_qr_data(result, raw_data, mimetype)
        # Document qualification
        self._ai_qualify_document(result)
        if result['is_proforma']:
            return result
        # Table extraction (still use pdfplumber if available)
        self._ai_extract_tables(result, raw_data, mimetype, extract_lines)
        return result

    @staticmethod
    def _ai_format_preprocess_data(preprocess_result):
        """Format pre-processor structured data as context for Claude prompt."""
        data = preprocess_result.get('data', {})
        source = data.get('_source', preprocess_result.get('provider', 'preprocessor'))
        confidence = preprocess_result.get('confidence', 0.0)
        parts = [
            'PRE-PROCESSED DATA (source: %s, overall confidence: %.0f%%):' % (source, confidence * 100),
            'Use this as a strong reference but validate against the document.',
            'If the pre-processed data conflicts with what you see in the document, prefer the document.',
        ]
        vendor = data.get('vendor', {})
        if vendor.get('name'):
            parts.append(
                'Vendor: %s (VAT: %s, confidence: %.0f%%)'
                % (vendor['name'], vendor.get('vat', 'N/A'), vendor.get('confidence', 0) * 100)
            )
        invoice = data.get('invoice', {})
        if invoice.get('reference'):
            parts.append(
                'Reference: %s (date: %s, due: %s)'
                % (invoice['reference'], invoice.get('invoice_date', 'N/A'), invoice.get('due_date', 'N/A'))
            )
        totals = data.get('totals', {})
        if totals.get('total_amount'):
            parts.append(
                'Totals: untaxed=%.2f, tax=%.2f, total=%.2f'
                % (totals.get('untaxed_amount', 0), totals.get('tax_amount', 0), totals.get('total_amount', 0))
            )
        lines = data.get('lines', [])
        if lines:
            parts.append('Line items (%d):' % len(lines))
            for i, line in enumerate(lines[:20]):
                parts.append(
                    '  %d. %s | qty=%s | price=%s | subtotal=%s'
                    % (
                        i + 1,
                        line.get('description', '?'),
                        line.get('quantity', '?'),
                        line.get('unit_price', '?'),
                        line.get('subtotal_untaxed', '?'),
                    )
                )
        return '\n'.join(parts)

    def _ai_log_preprocess(self, provider_name, result):
        """Log pre-processor result to extraction log (debug mode)."""
        raw = result.get('raw_response', {})
        cost = result.get('cost_per_page', 0.0) * result.get('page_count', 1)
        self.env['ai.extraction.log'].create(
            {
                'move_id': self.id,
                'prompt_sent': '[Pre-processor: %s]' % provider_name,
                'response_received': json.dumps(raw, default=str)[:50000],
                'model_used': provider_name,
                'input_tokens': 0,
                'output_tokens': 0,
                'cost_estimated': cost,
                'success': result.get('success', False),
            }
        )
