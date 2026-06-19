import json
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

# Realistic mock response for a French vendor bill
MOCK_CLAUDE_RESPONSE = {
    'id': 'msg_test123',
    'type': 'message',
    'role': 'assistant',
    'model': 'claude-haiku-4-5-20251001',
    'usage': {'input_tokens': 1500, 'output_tokens': 600},
    'content': [
        {
            'type': 'text',
            'text': json.dumps(
                {
                    'document_type': 'invoice',
                    'is_marked_paid': False,
                    'vendor': {
                        'name': 'ACME Services SARL',
                        'vat': 'FR12345678901',
                        'address': '12 rue de la Paix, 75001 Paris',
                        'email': 'contact@acme-services.fr',
                        'phone': None,
                        'website': None,
                        'iban': None,
                        'bic': None,
                        'bank_name': None,
                        'confidence': 0.95,
                    },
                    'buyer': {
                        'name': 'Ma Société SAS',
                        'vat': None,
                        'address': None,
                        'confidence': 0.9,
                    },
                    'invoice': {
                        'reference': 'FAC-2024-001',
                        'invoice_date': '2024-01-15',
                        'invoice_date_raw': '15/01/2024',
                        'due_date': '2024-02-15',
                        'due_date_raw': '15/02/2024',
                        'currency': 'EUR',
                        'payment_reference': 'FAC-2024-001',
                        'payment_terms_text': '30 jours',
                        'payment_method': 'bank_transfer',
                        'purchase_order_ref': None,
                        'delivery_note_ref': None,
                        'narration': None,
                        'is_credit_note': False,
                        'is_reverse_charge': False,
                        'reverse_charge_text': None,
                        'original_invoice_ref': None,
                        'confidence': 0.92,
                    },
                    'totals': {
                        'untaxed_amount': 1000.00,
                        'tax_amount': 200.00,
                        'total_amount': 1200.00,
                        'confidence': 0.98,
                    },
                    'tax_lines': [
                        {
                            'tax_label': 'TVA 20%',
                            'tax_rate': 20.0,
                            'base_amount': 1000.00,
                            'tax_amount': 200.00,
                            'confidence': 0.95,
                        },
                    ],
                    'table_analysis': {
                        'columns_detected': ['description', 'quantity', 'unit_price_ht', 'total_ht'],
                        'pricing_mode': 'ht_to_ttc',
                        'tax_display': 'grouped_at_bottom',
                        'has_discounts': False,
                        'has_deposits': False,
                        'has_early_payment_discount': False,
                        'has_shipping_line': False,
                        'line_count': 2,
                        'complexity': 'simple',
                        'number_format': 'comma_decimal',
                        'date_format_detected': 'DD/MM/YYYY',
                        'tax_info_per_line': 'none',
                        'has_tax_summary_table': True,
                    },
                    'lines': [
                        {
                            'description': 'Consulting services - January 2024',
                            'product_code': None,
                            'unit_of_measure': None,
                            'quantity': 10,
                            'unit_price': 80.00,
                            'unit_price_is_tax_included': False,
                            'discount_percent': None,
                            'discount_amount': None,
                            'subtotal_untaxed': 800.00,
                            'subtotal_tax_included': None,
                            'tax_rate': 20.0,
                            'tax_amount': None,
                            'is_shipping_line': False,
                            'suggested_account_category': 'consulting',
                            'confidence': 0.9,
                        },
                        {
                            'description': 'Travel expenses',
                            'product_code': None,
                            'unit_of_measure': None,
                            'quantity': 1,
                            'unit_price': 200.00,
                            'unit_price_is_tax_included': False,
                            'discount_percent': None,
                            'discount_amount': None,
                            'subtotal_untaxed': 200.00,
                            'subtotal_tax_included': None,
                            'tax_rate': 20.0,
                            'tax_amount': None,
                            'is_shipping_line': False,
                            'suggested_account_category': 'travel',
                            'confidence': 0.85,
                        },
                    ],
                }
            ),
        },
    ],
}

# Mock response for API errors
MOCK_CLAUDE_ERROR_401 = MagicMock(status_code=401, json=lambda: {})
MOCK_CLAUDE_ERROR_429 = MagicMock(status_code=429, json=lambda: {})


def _make_mock_response(status_code=200, data=None):
    """Create a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data or MOCK_CLAUDE_RESPONSE
    return mock


@tagged('post_install', '-at_install')
class TestExtraction(TransactionCase):
    """Test the AI extraction pipeline with mocked API calls."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # Create a test partner (vendor)
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'ACME Services SARL',
                'is_company': True,
                'vat': 'FR12345678901',
            }
        )
        # Create a purchase tax
        cls.tax_20 = cls.env['account.tax'].create(
            {
                'name': 'TVA 20%',
                'amount': 20.0,
                'type_tax_use': 'purchase',
                'company_id': cls.company.id,
            }
        )
        # Set API key
        cls.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', 'test-api-key-123')

    def _create_vendor_bill(self):
        """Helper: create a draft vendor bill."""
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )

    def _attach_pdf(self, move):
        """Helper: attach a fake PDF to an invoice."""
        # Minimal valid PDF bytes (enough to not crash, too short for real extraction)
        import base64

        fake_pdf = base64.b64encode(b'%PDF-1.4 fake content for testing')
        self.env['ir.attachment'].create(
            {
                'name': 'test_invoice.pdf',
                'datas': fake_pdf,
                'mimetype': 'application/pdf',
                'res_model': 'account.move',
                'res_id': move.id,
            }
        )

    def _extract_direct(self, move):
        """Run extraction pipeline directly (non-preview) for testing."""
        api_key = self.env['ir.config_parameter'].sudo().get_param('account_invoice_digitize_ai.ai_api_key')
        attachment = move._ai_get_invoice_attachment()
        move._ai_trigger_extraction(api_key, attachment)

    # ------------------------------------------------------------------
    # Prerequisites checks
    # ------------------------------------------------------------------

    def test_no_api_key_sets_status(self):
        """Without API key, status should be 'no_api'."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', '')
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        move.action_ai_extract()
        self.assertEqual(move.ai_extraction_status, 'no_api')

    def test_no_attachment_returns_warning(self):
        """Without attachment, should return a warning notification."""
        move = self._create_vendor_bill()
        result = move.action_ai_extract()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertIn('attach', result['params']['message'].lower())

    # ------------------------------------------------------------------
    # Successful extraction
    # ------------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_successful_extraction_sets_done(self, mock_post):
        """A successful API call should set status to 'done'."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertEqual(move.ai_extraction_status, 'done')

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_extraction_fills_reference(self, mock_post):
        """Extracted invoice reference should be mapped to the ref field."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertEqual(move.ref, 'FAC-2024-001')

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_extraction_fills_dates(self, mock_post):
        """Invoice date and due date should be filled."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertEqual(str(move.invoice_date), '2024-01-15')
        # Due date may be recomputed by matched payment terms (Odoo 19+)
        self.assertTrue(move.invoice_date_due, 'Due date should be set')

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_extraction_matches_partner_by_vat(self, mock_post):
        """Vendor should be matched by VAT number."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertEqual(move.partner_id, self.partner)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_extraction_stores_confidence(self, mock_post):
        """Confidence scores should be stored as JSON."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertTrue(move.ai_confidence)
        confidence = json.loads(move.ai_confidence)
        self.assertIn('partner_id', confidence)
        self.assertIn('totals', confidence)

    # ------------------------------------------------------------------
    # API error handling
    # ------------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_auth_error_sets_no_api(self, mock_post):
        """HTTP 401 should set status to 'no_api'."""
        mock_post.return_value = _make_mock_response(401)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertEqual(move.ai_extraction_status, 'no_api')

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_server_error_sets_failed(self, mock_post):
        """HTTP 500 should set status to 'failed'."""
        mock_post.return_value = _make_mock_response(500)
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertEqual(move.ai_extraction_status, 'failed')

    # ------------------------------------------------------------------
    # Partner matching (via ai_matcher)
    # ------------------------------------------------------------------

    def test_partner_match_by_vat(self):
        """match_partner should find partner by VAT number."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_partner

        partner = match_partner(self.env, {'vat': 'FR12345678901'})
        self.assertEqual(partner, self.partner)

    def test_partner_match_by_name(self):
        """match_partner should fallback to name match."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_partner

        partner = match_partner(self.env, {'name': 'ACME Services SARL'})
        self.assertEqual(partner, self.partner)

    def test_partner_no_match(self):
        """match_partner should return None for unknown vendor."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_partner

        partner = match_partner(self.env, {'name': 'Unknown Corp', 'vat': 'XX000000000'})
        self.assertFalse(partner)

    # ------------------------------------------------------------------
    # Tax matching (via ai_matcher)
    # ------------------------------------------------------------------

    def test_tax_match_exact_rate(self):
        """match_tax_by_rate should find tax by exact rate."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_tax_by_rate

        tax = match_tax_by_rate(self.env, 20.0, self.company)
        self.assertTrue(tax)
        self.assertEqual(tax.amount, 20.0)

    def test_tax_match_approximate_rate(self):
        """match_tax_by_rate should find tax within 0.5% tolerance."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_tax_by_rate

        tax = match_tax_by_rate(self.env, 20.1, self.company)
        self.assertTrue(tax)
        self.assertEqual(tax.amount, 20.0)

    def test_tax_no_match(self):
        """match_tax_by_rate should return None for unknown rate."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import match_tax_by_rate

        tax = match_tax_by_rate(self.env, 99.0, self.company)
        self.assertFalse(tax)

    # ------------------------------------------------------------------
    # Cross-validation (via ai_validator)
    # ------------------------------------------------------------------

    def test_cross_validate_totals_pass(self):
        """Cross-validation should pass when totals are consistent."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        data = {
            'totals': {
                'untaxed_amount': 1000.0,
                'tax_amount': 200.0,
                'total_amount': 1200.0,
                'confidence': 0.95,
            },
            'invoice': {
                'invoice_date': '2024-01-15',
                'due_date': '2024-02-15',
                'confidence': 0.9,
            },
        }
        cross_validate(data)
        # Confidence should remain unchanged
        self.assertEqual(data['totals']['confidence'], 0.95)

    def test_cross_validate_totals_fail(self):
        """Cross-validation should lower confidence when totals mismatch."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        data = {
            'totals': {
                'untaxed_amount': 1000.0,
                'tax_amount': 200.0,
                'total_amount': 1300.0,  # Wrong!
                'confidence': 0.95,
            },
            'invoice': {},
        }
        cross_validate(data)
        self.assertLessEqual(data['totals']['confidence'], 0.5)

    def test_cross_validate_dates_fail(self):
        """Cross-validation should flag when due_date < invoice_date."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_validator import cross_validate

        data = {
            'totals': {
                'untaxed_amount': 100.0,
                'tax_amount': 20.0,
                'total_amount': 120.0,
                'confidence': 0.95,
            },
            'invoice': {
                'invoice_date': '2024-02-15',
                'due_date': '2024-01-15',  # Before invoice date!
                'confidence': 0.9,
            },
        }
        cross_validate(data)
        self.assertLessEqual(data['invoice']['confidence'], 0.5)

    # ------------------------------------------------------------------
    # Debug logging
    # ------------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_debug_mode_creates_log(self, mock_post):
        """When debug mode is on, an extraction log should be created."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_debug_mode', 'True')
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertTrue(move.ai_extraction_log_id)
        self.assertTrue(move.ai_extraction_log_id.success)
        self.assertGreater(move.ai_extraction_log_id.input_tokens, 0)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_no_debug_mode_lightweight_log(self, mock_post):
        """When debug mode is off, a lightweight log should still be created (no prompt/response)."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_debug_mode', 'False')
        move = self._create_vendor_bill()
        self._attach_pdf(move)
        self._extract_direct(move)
        self.assertTrue(move.ai_extraction_log_id)
        # Lightweight log: no prompt/response content stored
        self.assertFalse(move.ai_extraction_log_id.prompt_sent)
        self.assertFalse(move.ai_extraction_log_id.response_received)


@tagged('post_install', '-at_install')
class TestDocumentUtils(TransactionCase):
    """Test document processing utilities."""

    def test_is_pdf(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import is_pdf

        self.assertTrue(is_pdf('application/pdf'))
        self.assertTrue(is_pdf('application/x-pdf'))
        self.assertFalse(is_pdf('image/png'))
        self.assertFalse(is_pdf(''))
        self.assertFalse(is_pdf(None))

    def test_is_image(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import is_image

        self.assertTrue(is_image('image/png'))
        self.assertTrue(is_image('image/jpeg'))
        self.assertFalse(is_image('application/pdf'))
        self.assertFalse(is_image('text/plain'))

    def test_find_vat_numbers(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import find_vat_numbers

        text = 'Vendor: ACME SARL, VAT: FR12345678901, Addr: Paris'
        vats = find_vat_numbers(text)
        self.assertIn('FR12345678901', vats)

    def test_find_vat_numbers_multiple(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import find_vat_numbers

        text = 'Seller: FR12345678901 Buyer: DE123456789'
        vats = find_vat_numbers(text)
        self.assertEqual(len(vats), 2)
        self.assertIn('FR12345678901', vats)
        self.assertIn('DE123456789', vats)

    def test_find_vat_numbers_none(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import find_vat_numbers

        vats = find_vat_numbers('No VAT numbers here')
        self.assertEqual(len(vats), 0)


@tagged('post_install', '-at_install')
class TestProviderFactory(TransactionCase):
    """Test the AI provider factory."""

    def test_get_anthropic_provider(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider import get_provider

        provider = get_provider('anthropic')
        self.assertEqual(provider.get_provider_name(), 'Anthropic (Claude)')
        self.assertTrue(provider.supports_vision())

    def test_get_all_providers(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider import get_provider

        for name in ('openai', 'google', 'xai', 'deepseek', 'mistral'):
            provider = get_provider(name)
            self.assertTrue(provider.get_available_models())

    def test_unknown_provider_raises(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider import get_provider

        with self.assertRaises(ValueError):
            get_provider('unknown_provider')

    def test_anthropic_cost_estimate(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_provider import get_provider

        provider = get_provider('anthropic')
        cost = provider.estimate_cost(2000, 800, 'claude-haiku-4-5-20251001')
        self.assertGreater(cost, 0)
        # Haiku should be cheaper than Opus for same tokens
        cost_opus = provider.estimate_cost(2000, 800, 'claude-opus-4-6')
        self.assertGreater(cost_opus, cost)


@tagged('post_install', '-at_install')
class TestEndToEndPipeline(TransactionCase):
    """End-to-end tests for the full extraction pipeline."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'ACME Services SARL',
                'is_company': True,
                'vat': 'FR12345678901',
            }
        )
        cls.tax_20 = cls.env['account.tax'].create(
            {
                'name': 'TVA 20%',
                'amount': 20.0,
                'type_tax_use': 'purchase',
                'company_id': cls.company.id,
            }
        )
        cls.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', 'test-api-key-123')
        cls.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_extract_lines', 'True')

    def _create_bill_with_pdf(self):
        import base64

        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        self.env['ir.attachment'].create(
            {
                'name': 'test.pdf',
                'datas': base64.b64encode(b'%PDF-1.4 fake content'),
                'mimetype': 'application/pdf',
                'res_model': 'account.move',
                'res_id': move.id,
            }
        )
        return move

    def _extract_direct(self, move):
        """Run extraction pipeline directly (non-preview) for testing."""
        api_key = self.env['ir.config_parameter'].sudo().get_param('account_invoice_digitize_ai.ai_api_key')
        attachment = move._ai_get_invoice_attachment()
        move._ai_trigger_extraction(api_key, attachment)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_e2e_full_pipeline(self, mock_post):
        """Full pipeline: extraction -> fields populated -> confidence -> lines created."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move = self._create_bill_with_pdf()
        self._extract_direct(move)

        # Status
        self.assertEqual(move.ai_extraction_status, 'done')
        # Partner matched
        self.assertEqual(move.partner_id, self.partner)
        # Reference
        self.assertEqual(move.ref, 'FAC-2024-001')
        # Dates
        self.assertEqual(str(move.invoice_date), '2024-01-15')
        # Confidence stored
        self.assertTrue(move.ai_confidence)
        conf = json.loads(move.ai_confidence)
        self.assertIn('partner_id', conf)
        # Lines created (2 lines in mock)
        product_lines = move.invoice_line_ids.filtered(lambda line: line.display_type in (False, 'product'))
        self.assertEqual(len(product_lines), 2)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_e2e_credit_note_detection(self, mock_post):
        """Credit note document_type should set move_type to in_refund."""
        # Build credit note response
        credit_response = json.loads(MOCK_CLAUDE_RESPONSE['content'][0]['text'])
        credit_response['document_type'] = 'credit_note'
        credit_response['invoice']['is_credit_note'] = True
        mock_data = dict(MOCK_CLAUDE_RESPONSE)
        mock_data['content'] = [{'type': 'text', 'text': json.dumps(credit_response)}]
        mock_post.return_value = _make_mock_response(200, mock_data)

        move = self._create_bill_with_pdf()
        self._extract_direct(move)

        self.assertEqual(move.move_type, 'in_refund')
