import base64
import logging
import time as time_mod

from odoo import models

from . import ai_document
from .ai_vendor_memory import AiVendorMemory

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ===================================================================
    # Prompt size estimation
    # ===================================================================

    @staticmethod
    def _ai_estimate_prompt_tokens(system_prompt, user_prompt):
        """Estimate the number of input tokens for the prompt.

        Uses a simple heuristic: ~4 characters per token for English/French
        text. This is a rough estimate for cost awareness, not billing.
        """
        total_chars = len(system_prompt or '') + len(user_prompt or '')
        return total_chars // 4

    # ===================================================================
    # Document preparation
    # ===================================================================

    def _ai_prepare_document(self, raw_data, mimetype, extract_lines, extract_qr_codes=True):
        """Pre-process document: extract text, metadata, tables, qualify.

        Returns a dict with keys: text, is_vision, pdf_metadata,
        detected_number_format, table_markdown, is_proforma, unsupported, qr_data.
        """
        result = {
            'text': '',
            'is_vision': False,
            'pdf_metadata': {},
            'detected_number_format': None,
            'detected_language': None,
            'detected_language_name': None,
            'table_markdown': '',
            'is_proforma': False,
            'unsupported': False,
            'qr_data': [],
        }

        # Text extraction (PDF) or vision (image)
        if ai_document.is_pdf(mimetype):
            text = ai_document.extract_text_from_pdf(raw_data)
            result['text'] = text or ''
            if not text or len(text.strip()) < 50:
                result['is_vision'] = True
        elif ai_document.is_image(mimetype):
            result['is_vision'] = True
        else:
            _logger.warning('Unsupported attachment type: %s', mimetype)
            result['unsupported'] = True
            return result

        # PDF metadata
        if ai_document.is_pdf(mimetype):
            result['pdf_metadata'] = ai_document.extract_pdf_metadata(raw_data)
            if result['pdf_metadata']:
                _logger.info('PDF metadata: %s', result['pdf_metadata'])

        # Text heuristics: number format, language (text-based PDFs only)
        self._ai_detect_text_heuristics(result)

        # QR code extraction (PDF only, before qualification)
        if extract_qr_codes:
            self._ai_extract_qr_data(result, raw_data, mimetype)

        # Document qualification (text-based PDFs only)
        self._ai_qualify_document(result)
        if result['is_proforma']:
            return result

        # Structured table extraction (text-based PDFs, lines enabled)
        self._ai_extract_tables(result, raw_data, mimetype, extract_lines)

        return result

    @staticmethod
    def _ai_detect_text_heuristics(result):
        """Detect number format and language from text (mutates *result*)."""
        if not result['text'] or result['is_vision']:
            return
        fmt = ai_document.detect_number_format(result['text'])
        if fmt:
            result['detected_number_format'] = fmt
            _logger.info('Detected number format: %s', fmt)
        lang_code, lang_name = ai_document.detect_language(result['text'])
        if lang_code:
            result['detected_language'] = lang_code
            result['detected_language_name'] = lang_name
            _logger.info('Detected document language: %s (%s)', lang_name, lang_code)

    @staticmethod
    def _ai_extract_qr_data(result, raw_data, mimetype):
        """Extract QR codes from PDF images (mutates *result*)."""
        from . import ai_qr_decoder

        if not ai_qr_decoder.PYZBAR_AVAILABLE:
            return
        if not ai_document.is_pdf(mimetype):
            return
        payloads = ai_qr_decoder.extract_qr_from_pdf(raw_data)
        for payload in payloads:
            parsed = ai_qr_decoder.parse_qr_payload(payload)
            if parsed['format'] != 'unknown':
                result['qr_data'].append(parsed)
        if result['qr_data']:
            _logger.info('Extracted %d QR code(s)', len(result['qr_data']))

    @staticmethod
    def _ai_qualify_document(result):
        """Run document qualification on text-based documents (mutates *result*)."""
        if not result['text'] or result['is_vision']:
            return
        from . import ai_document_qualifier

        qual = ai_document_qualifier.qualify_document(result['text'])
        if qual.get('is_proforma'):
            result['is_proforma'] = True

    @staticmethod
    def _ai_extract_tables(result, raw_data, mimetype, extract_lines):
        """Extract structured tables from text-based PDFs (mutates *result*)."""
        if not (extract_lines and result['text'] and not result['is_vision']):
            return
        if not (ai_document.PDFPLUMBER_AVAILABLE and ai_document.is_pdf(mimetype)):
            return
        tables = ai_document.extract_tables_from_pdf(raw_data)
        if tables:
            result['table_markdown'] = ai_document.format_tables_as_markdown(tables)
            _logger.info('Extracted %d table(s) via pdfplumber', len(tables))

    # ===================================================================
    # Prompt building
    # ===================================================================

    def _ai_build_content(
        self, doc_info, raw_data, mimetype, vendor, company, extract_lines, preprocess_context='', cfg=None
    ):
        """Build prompt and content blocks for the AI provider.

        Args:
            preprocess_context: Optional pre-processor data formatted as text
                for Claude Enrichment mode.
            cfg: Optional config dict (avoids re-reading ICP).

        Returns (system_prompt, user_content, user_prompt_text).
        """
        from .ai_prompt import (
            EXTRACTION_SCHEMA,
            EXTRACTION_SCHEMA_NO_LINES,
            SYSTEM_PROMPT,
        )

        from . import ai_fiscal_context

        cfg = cfg or self._ai_get_config()
        mode = cfg.get('extraction_mode', 'guided')
        schema = EXTRACTION_SCHEMA if extract_lines else EXTRACTION_SCHEMA_NO_LINES

        # Fiscal context depends on extraction mode
        if mode == 'guided':
            fiscal_context = ai_fiscal_context.build_fiscal_context(self.env, company, vendor)
        elif mode == 'simplified':
            fiscal_context = ai_fiscal_context.build_fiscal_context(
                self.env,
                company,
                vendor,
                include_accounts=False,
            )
        else:  # free
            fiscal_context = ''

        # Vendor memory context (guided mode only)
        vendor_memory_ctx = ''
        if mode == 'guided' and vendor:
            vendor_memory_ctx = AiVendorMemory.get_vendor_context(self.env, vendor, company=company)

        # Pre-processing context (metadata + number format + language + QR)
        preprocess_ctx = self._ai_format_preprocess_context(
            doc_info.get('pdf_metadata', {}),
            doc_info.get('detected_number_format'),
            detected_language=doc_info.get('detected_language'),
            detected_language_name=doc_info.get('detected_language_name'),
            qr_data=doc_info.get('qr_data'),
        )

        user_prompt = (
            'Extract all data from the following invoice document.\n\n'
            + (preprocess_context + '\n\n' if preprocess_context else '')
            + (preprocess_ctx + '\n' if preprocess_ctx else '')
            + fiscal_context
            + '\n\n'
            + (vendor_memory_ctx + '\n\n' if vendor_memory_ctx else '')
            + schema
        )

        # Build content blocks for the provider
        if doc_info.get('is_vision'):
            media_type = ai_document.IMAGE_MIMES.get(mimetype, 'image/png')
            if ai_document.is_pdf(mimetype):
                media_type = 'image/png'
            user_content = [
                {
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': base64.b64encode(raw_data).decode('ascii'),
                    },
                },
                {'type': 'text', 'text': user_prompt},
            ]
        else:
            doc_section = 'Document text:\n' + doc_info['text']
            if doc_info.get('table_markdown'):
                doc_section += (
                    '\n\n---\n\n'
                    'Structured table data extracted from the PDF '
                    '(use this for line items — it preserves the original table structure):\n\n'
                    + doc_info['table_markdown']
                )
            user_content = [
                {'type': 'text', 'text': user_prompt + '\n\n---\n\n' + doc_section},
            ]

        return SYSTEM_PROMPT, user_content, user_prompt

    def _ai_format_preprocess_context(
        self,
        pdf_metadata,
        detected_number_format,
        detected_language=None,
        detected_language_name=None,
        qr_data=None,
    ):
        """Format PDF metadata, number format, language and QR data into prompt context string."""
        preprocess_ctx = self._ai_format_pdf_metadata(pdf_metadata)
        if detected_number_format:
            if detected_number_format == 'comma_decimal':
                fmt_label = 'comma as decimal separator (e.g. 1.234,56)'
            else:
                fmt_label = 'dot as decimal separator (e.g. 1,234.56)'
            preprocess_ctx += f'Detected number format: {fmt_label}\n'
        if detected_language and detected_language_name:
            preprocess_ctx += f'Detected document language: {detected_language_name} ({detected_language})\n'
        if qr_data:
            from . import ai_qr_decoder

            qr_ctx = ai_qr_decoder.format_qr_context(qr_data)
            if qr_ctx:
                preprocess_ctx += qr_ctx + '\n'
        return preprocess_ctx

    @staticmethod
    def _ai_format_pdf_metadata(pdf_metadata):
        """Format PDF metadata dict into a single-line context string."""
        if not pdf_metadata:
            return ''
        parts = []
        if pdf_metadata.get('creator'):
            parts.append('Creator software: ' + pdf_metadata['creator'])
        if pdf_metadata.get('author'):
            parts.append('Author: ' + pdf_metadata['author'])
        if pdf_metadata.get('title'):
            parts.append('Title: ' + pdf_metadata['title'])
        if pdf_metadata.get('creation_date'):
            parts.append('PDF creation date: ' + pdf_metadata['creation_date'])
        if parts:
            return 'PDF metadata: ' + '; '.join(parts) + '\n'
        return ''

    # ===================================================================
    # Extraction log
    # ===================================================================

    def _ai_create_log(
        self,
        prompt,
        result,
        provider_name=None,
        model_id=None,
        debug_mode=False,
        start_time=None,
        mode='text',
    ):
        """Create an extraction log record.

        Always creates a lightweight log (vendor, confidence, duration, cost).
        Only stores prompt/response content when *debug_mode* is True.
        """
        from .ai_provider import get_provider

        if not provider_name or not model_id:
            cfg = self._ai_get_config()
            provider_name = provider_name or cfg['provider_name']
            model_id = model_id or cfg['model_id']

        provider = get_provider(provider_name)
        cost = provider.estimate_cost(
            result.get('input_tokens', 0),
            result.get('output_tokens', 0),
            model_id,
        )

        # Extract lightweight info from result data
        data = result.get('data') or {}
        vendor_name = ''
        overall_confidence = 0.0
        if isinstance(data, dict):
            vendor_name = (data.get('vendor') or {}).get('name', '')
            overall_confidence = (data.get('totals') or {}).get('confidence', 0.0)

        duration = round(time_mod.time() - start_time, 2) if start_time else 0.0

        vals = {
            'move_id': self.id,
            'model_used': result.get('model', model_id),
            'input_tokens': result.get('input_tokens', 0),
            'output_tokens': result.get('output_tokens', 0),
            'cost_estimated': cost,
            'success': result.get('success', False),
            'vendor_name': vendor_name,
            'overall_confidence': overall_confidence,
            'duration_seconds': duration,
            'provider_name': provider_name,
            'extraction_mode': mode,
            'error_message': result.get('message', '') if not result.get('success') else '',
        }

        # Debug mode: include full prompt and response (truncated to 50 KB)
        if debug_mode:
            vals['prompt_sent'] = (prompt or '')[:50_000]
            vals['response_received'] = (result.get('raw_text', '') or '')[:50_000]

        log = self.env['ai.extraction.log'].create(vals)
        self.ai_extraction_log_id = log.id
