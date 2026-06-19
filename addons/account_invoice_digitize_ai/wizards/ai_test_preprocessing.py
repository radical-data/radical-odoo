"""Test wizard — Document Recognition (preprocessing) mode.

Tests external document recognition providers (Azure DI, AWS Textract)
on an uploaded document. Shows confidence, page count, cost, and structured data.
"""

import json
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AiTestWizard(models.TransientModel):
    _inherit = 'ai.test.wizard'

    def _test_preprocessing(self):
        """Test external pre-processor (Azure DI / AWS Textract)."""
        ICP = self.env['ir.config_parameter'].sudo()
        provider_name = ICP.get_param('account_invoice_digitize_ai.ai_preprocess_provider', 'none')

        if provider_name == 'none':
            self.result_status = 'failed'
            self.result_message = self.env._(
                'No recognition service configured. Go to Settings > AI Invoice Digitization'
                ' > Document Recognition to configure Azure Document Intelligence or AWS Textract.',
            )
            return

        raw_data, mimetype = self._get_uploaded_document()

        move = self.env['account.move'].new({'move_type': 'in_invoice'})
        credentials = move._ai_get_preprocess_credentials(provider_name)
        if not credentials:
            self.result_status = 'failed'
            self.result_message = self.env._(
                'No credentials configured for %s. Check your settings.',
                provider_name,
            )
            return

        from ..models.ai_preprocessor import get_preprocessor

        preprocessor = get_preprocessor(provider_name)
        pp_result = preprocessor.extract_structured(credentials, raw_data, mimetype)

        if not pp_result.get('success'):
            self.result_status = 'failed'
            self.result_message = self.env._(
                'Recognition failed: %s',
                pp_result.get('error', 'Unknown error'),
            )
            return

        details = self._format_preprocessing_details(pp_result, provider_name)
        confidence = pp_result.get('confidence', 0.0)
        pages = pp_result.get('page_count', 1)
        cost = pp_result.get('cost_per_page', 0.0) * pages
        self.result_status = 'success'
        self.result_message = self.env._(
            'Recognition successful: %.0f%% confidence, %s page(s), $%.4f cost.',
            confidence * 100,
            pages,
            cost,
        )
        self.result_details = '\n'.join(details)

    def _format_preprocessing_details(self, pp_result, provider_name):
        """Build detailed output for pre-processing mode."""
        _ = self.env._
        details = []
        details.append(_('Provider: %s', provider_name))
        details.append(_('Confidence: %.1f%%', pp_result.get('confidence', 0.0) * 100))
        details.append(_('Pages: %s', pp_result.get('page_count', 1)))
        cost = pp_result.get('cost_per_page', 0.0) * pp_result.get('page_count', 1)
        details.append(_('Cost: $%.4f', cost))

        # Extracted text
        text = pp_result.get('text', '')
        details.append('')
        details.append('--- %s ---' % _('Extracted Text (first 3000 chars)'))
        details.append(text[:3000] if text else _('(no text)'))

        # Structured data
        data = pp_result.get('data')
        if data:
            details.append('')
            details.append('--- %s ---' % _('Structured Data'))
            details.append(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:5000])

        return details
