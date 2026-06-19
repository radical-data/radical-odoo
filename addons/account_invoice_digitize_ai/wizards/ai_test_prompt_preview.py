"""Test wizard — Prompt Preview mode.

Builds the full extraction prompt without calling the AI API.
Shows system prompt, user prompt, estimated tokens, and cost. Zero API cost.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AiTestWizard(models.TransientModel):
    _inherit = 'ai.test.wizard'

    def _test_prompt_preview(self):
        """Build the full prompt without calling the API. Zero cost."""
        from ..models import ai_document
        from .ai_test_wizard import SAMPLE_INVOICE_TEXT

        ICP = self.env['ir.config_parameter'].sudo()
        extract_lines = ICP.get_param('account_invoice_digitize_ai.ai_extract_lines', 'False') == 'True'
        provider_name = ICP.get_param('account_invoice_digitize_ai.ai_provider', 'anthropic')

        move = self.env['account.move'].new({'move_type': 'in_invoice'})

        if self.use_sample or not self.test_document:
            doc_info = {
                'text': SAMPLE_INVOICE_TEXT,
                'is_vision': False,
                'pdf_metadata': {},
                'detected_number_format': ai_document.detect_number_format(SAMPLE_INVOICE_TEXT),
                'table_markdown': '',
                'is_proforma': False,
                'unsupported': False,
            }
            raw_data = SAMPLE_INVOICE_TEXT.encode('utf-8')
            mimetype = 'application/pdf'
            source_label = self.env._('sample invoice')
        else:
            raw_data, mimetype = self._get_uploaded_document()
            doc_info = move._ai_prepare_document(raw_data, mimetype, extract_lines)
            if doc_info.get('unsupported'):
                self.result_status = 'failed'
                self.result_message = self.env._('Unsupported file type: %s', mimetype)
                return
            source_label = self.test_document_name or self.env._('uploaded document')

        vendor = move._ai_pre_identify_vendor(doc_info.get('text', ''))
        company = self.env.company
        system_prompt, user_content, user_prompt = move._ai_build_content(
            doc_info, raw_data, mimetype, vendor, company, extract_lines
        )

        estimated_tokens = move._ai_estimate_prompt_tokens(system_prompt, user_prompt)

        # Estimate cost
        from ..models.ai_provider import get_provider

        _ = self.env._
        provider = get_provider(provider_name)
        cost = provider.estimate_cost(estimated_tokens, 800, None)

        details = []
        details.append(_('Source: %s', source_label))
        vision_label = _('Yes') if doc_info.get('is_vision') else _('No')
        details.append(_('Vision mode: %s', vision_label))
        lines_label = _('Yes') if extract_lines else _('No')
        details.append(_('Extract lines: %s', lines_label))
        vendor_label = vendor.name if vendor else _('none')
        details.append(_('Pre-identified vendor: %s', vendor_label))
        details.append(_('Estimated input tokens: ~%s', estimated_tokens))
        details.append(_('Estimated cost: $%.4f (with ~800 output tokens)', cost))
        details.append('')
        details.append('=== %s (%s) ===' % (_('SYSTEM PROMPT'), _('%s chars', len(system_prompt))))
        details.append(system_prompt[:5000])
        if len(system_prompt) > 5000:
            details.append('... [%s]' % _('truncated, %s chars total', len(system_prompt)))
        details.append('')
        details.append('=== %s (%s) ===' % (_('USER PROMPT'), _('%s chars', len(user_prompt))))
        details.append(user_prompt[:10000])
        if len(user_prompt) > 10000:
            details.append('... [%s]' % _('truncated, %s chars total', len(user_prompt)))

        self.result_status = 'success'
        self.result_message = _('Prompt built: ~%s estimated input tokens ($%.4f).', estimated_tokens, cost)
        self.result_details = '\n'.join(details)
