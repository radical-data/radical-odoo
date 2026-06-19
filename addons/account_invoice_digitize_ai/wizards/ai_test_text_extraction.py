"""Test wizard — Text Extraction mode.

Extracts text from an uploaded document without calling the AI API.
Shows page count, vision mode, number format, metadata, tables, and text preview.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AiTestWizard(models.TransientModel):
    _inherit = 'ai.test.wizard'

    def _test_text_extraction(self):
        """Test text extraction from an uploaded document. No API call."""
        raw_data, mimetype = self._get_uploaded_document()
        from ..models import ai_document

        ICP = self.env['ir.config_parameter'].sudo()
        extract_lines = ICP.get_param('account_invoice_digitize_ai.ai_extract_lines', 'False') == 'True'

        move = self.env['account.move'].new({'move_type': 'in_invoice'})
        doc_info = move._ai_prepare_document(raw_data, mimetype, extract_lines)

        if doc_info.get('unsupported'):
            self.result_status = 'failed'
            self.result_message = self.env._('Unsupported file type: %s', mimetype)
            return

        details = self._format_text_extraction_details(doc_info, raw_data, mimetype, ai_document)
        self.result_status = 'success'
        self.result_message = self._text_extraction_summary(doc_info)
        self.result_details = '\n'.join(details)

    def _format_text_extraction_details(self, doc_info, raw_data, mimetype, ai_document):
        """Build detailed output for text extraction mode."""
        _ = self.env._
        details = []
        text = doc_info.get('text', '')

        # Page count
        page_breaks = text.count('--- Page break ---') if text else 0
        pages = page_breaks + 1 if text else 0
        details.append(_('Pages: %s', pages))
        details.append(_('Characters extracted: %s', len(text)))
        vision_label = _('Yes (scanned/image)') if doc_info['is_vision'] else _('No (text-based)')
        details.append(_('Vision mode: %s', vision_label))

        # Number format
        fmt = doc_info.get('detected_number_format')
        details.append(_('Number format: %s', fmt or _('not detected')))

        # PDF metadata
        meta = doc_info.get('pdf_metadata', {})
        if meta:
            details.append('')
            details.append('--- %s ---' % _('PDF Metadata'))
            for key in ('creator', 'author', 'title', 'creation_date'):
                if meta.get(key):
                    details.append('%s: %s' % (key.replace('_', ' ').title(), meta[key]))

        # Document qualification
        details.append('')
        details.append('--- %s ---' % _('Qualification'))
        proforma_label = _('Yes') if doc_info.get('is_proforma') else _('No')
        details.append(_('Pro-forma detected: %s', proforma_label))

        # VAT numbers found
        if text:
            vat_numbers = ai_document.find_vat_numbers(text)
            vat_label = ', '.join(vat_numbers) if vat_numbers else _('none')
            details.append(_('VAT numbers found: %s', vat_label))

        # Tables
        tables_md = doc_info.get('table_markdown', '')
        pdfplumber_note = _('available') if ai_document.PDFPLUMBER_AVAILABLE else _('not installed')
        details.append('')
        details.append('--- %s (pdfplumber: %s) ---' % (_('Table Extraction'), pdfplumber_note))
        if tables_md:
            details.append(tables_md[:3000])
        else:
            details.append(_('No tables extracted.'))

        # Extracted text preview
        details.append('')
        details.append('--- %s ---' % _('Extracted Text (first 3000 chars)'))
        details.append(text[:3000] if text else _('(empty — vision mode)'))

        return details

    def _text_extraction_summary(self, doc_info):
        """One-line summary for text extraction results."""
        text = doc_info.get('text', '')
        if doc_info.get('is_vision'):
            if text:
                return self.env._(
                    'Document partially scanned: %s characters extracted, vision mode enabled.',
                    len(text),
                )
            return self.env._('Document is a scan/image. No text extracted — vision mode will be used.')
        return self.env._('Text extraction completed: %s characters extracted.', len(text))
