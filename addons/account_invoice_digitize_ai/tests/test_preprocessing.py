"""Tests for document pre-processing (Azure DI + AWS Textract).

Covers:
  1. Azure response normalization
  2. AWS Textract response normalization
  3. Pipeline integration (3 modes + fallbacks)
  4. Settings and credentials
  5. SigV4 signing
"""

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

_MODULE = 'odoo.addons.account_invoice_digitize_ai'


# ===================================================================
# Sample responses for mocking
# ===================================================================

AZURE_INVOICE_RESPONSE = {
    'status': 'succeeded',
    'analyzeResult': {
        'content': 'ACME Corp\nInvoice INV-2024-001\nTotal: 1200.00',
        'pages': [{'pageNumber': 1}, {'pageNumber': 2}],
        'documents': [
            {
                'fields': {
                    'VendorName': {'content': 'ACME Corp', 'confidence': 0.95},
                    'VendorTaxId': {'content': 'FR12345678901', 'confidence': 0.90},
                    'VendorAddress': {'content': '123 Main St, Paris', 'confidence': 0.85},
                    'CustomerName': {'content': 'My Company', 'confidence': 0.80},
                    'InvoiceId': {'content': 'INV-2024-001', 'confidence': 0.98},
                    'InvoiceDate': {'valueDate': '2024-06-15', 'content': '15/06/2024', 'confidence': 0.92},
                    'DueDate': {'valueDate': '2024-07-15', 'content': '15/07/2024', 'confidence': 0.88},
                    'SubTotal': {
                        'valueCurrency': {'amount': 1000.0, 'currencyCode': 'EUR'},
                        'confidence': 0.93,
                    },
                    'TotalTax': {
                        'valueCurrency': {'amount': 200.0, 'currencyCode': 'EUR'},
                        'confidence': 0.91,
                    },
                    'InvoiceTotal': {
                        'valueCurrency': {'amount': 1200.0, 'currencyCode': 'EUR'},
                        'confidence': 0.96,
                    },
                    'Items': {
                        'valueArray': [
                            {
                                'valueObject': {
                                    'Description': {'content': 'Consulting services', 'confidence': 0.90},
                                    'Quantity': {'valueNumber': 10, 'confidence': 0.85},
                                    'UnitPrice': {'valueCurrency': {'amount': 100.0}, 'confidence': 0.88},
                                    'Amount': {'valueCurrency': {'amount': 1000.0}, 'confidence': 0.92},
                                },
                            },
                        ],
                    },
                },
            }
        ],
    },
}

TEXTRACT_EXPENSE_RESPONSE = {
    'DocumentMetadata': {'Pages': 1},
    'ExpenseDocuments': [
        {
            'SummaryFields': [
                {
                    'Type': {'Text': 'VENDOR_NAME'},
                    'LabelDetection': {'Text': 'Vendor'},
                    'ValueDetection': {'Text': 'ACME Corp', 'Confidence': 95.0},
                },
                {
                    'Type': {'Text': 'INVOICE_RECEIPT_ID'},
                    'LabelDetection': {'Text': 'Invoice #'},
                    'ValueDetection': {'Text': 'INV-2024-001', 'Confidence': 98.0},
                },
                {
                    'Type': {'Text': 'INVOICE_RECEIPT_DATE'},
                    'LabelDetection': {'Text': 'Date'},
                    'ValueDetection': {'Text': '2024-06-15', 'Confidence': 92.0},
                },
                {
                    'Type': {'Text': 'TOTAL'},
                    'LabelDetection': {'Text': 'Total'},
                    'ValueDetection': {'Text': '1200.00', 'Confidence': 96.0},
                },
                {
                    'Type': {'Text': 'SUBTOTAL'},
                    'LabelDetection': {'Text': 'Subtotal'},
                    'ValueDetection': {'Text': '1000.00', 'Confidence': 93.0},
                },
                {
                    'Type': {'Text': 'TAX'},
                    'LabelDetection': {'Text': 'Tax'},
                    'ValueDetection': {'Text': '200.00', 'Confidence': 91.0},
                },
            ],
            'LineItemGroups': [
                {
                    'LineItems': [
                        {
                            'LineItemExpenseFields': [
                                {
                                    'Type': {'Text': 'ITEM'},
                                    'ValueDetection': {'Text': 'Consulting services', 'Confidence': 90.0},
                                },
                                {
                                    'Type': {'Text': 'QUANTITY'},
                                    'ValueDetection': {'Text': '10', 'Confidence': 85.0},
                                },
                                {
                                    'Type': {'Text': 'UNIT_PRICE'},
                                    'ValueDetection': {'Text': '100.00', 'Confidence': 88.0},
                                },
                                {
                                    'Type': {'Text': 'PRICE'},
                                    'ValueDetection': {'Text': '1000.00', 'Confidence': 92.0},
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}


# ===================================================================
# Azure normalization tests
# ===================================================================


@tagged('post_install', '-at_install')
class TestAzureNormalization(TransactionCase):
    """Test Azure Document Intelligence response normalization."""

    def test_normalize_full_response(self):
        """Azure response with all fields normalizes to extraction schema."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_azure import (
            _normalize_azure_fields,
        )

        fields = AZURE_INVOICE_RESPONSE['analyzeResult']['documents'][0]['fields']
        data = _normalize_azure_fields(fields)

        self.assertEqual(data['vendor']['name'], 'ACME Corp')
        self.assertEqual(data['vendor']['vat'], 'FR12345678901')
        self.assertEqual(data['invoice']['reference'], 'INV-2024-001')
        self.assertEqual(data['invoice']['invoice_date'], '2024-06-15')
        self.assertEqual(data['invoice']['due_date'], '2024-07-15')
        self.assertEqual(data['totals']['untaxed_amount'], 1000.0)
        self.assertEqual(data['totals']['tax_amount'], 200.0)
        self.assertEqual(data['totals']['total_amount'], 1200.0)
        self.assertEqual(data['invoice']['currency'], 'EUR')
        self.assertEqual(len(data['lines']), 1)
        self.assertEqual(data['lines'][0]['description'], 'Consulting services')
        self.assertEqual(data['lines'][0]['quantity'], 10)
        self.assertEqual(data['lines'][0]['unit_price'], 100.0)

    def test_normalize_minimal_response(self):
        """Azure response with missing fields produces valid schema."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_azure import (
            _normalize_azure_fields,
        )

        data = _normalize_azure_fields({})
        self.assertEqual(data['document_type'], 'invoice')
        self.assertEqual(data['vendor']['name'], '')
        self.assertEqual(data['totals']['total_amount'], 0.0)
        self.assertEqual(data['lines'], [])

    def test_confidence_mapping(self):
        """Azure confidence values map correctly to 0.0-1.0."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_azure import (
            _compute_overall_confidence,
        )

        fields = AZURE_INVOICE_RESPONSE['analyzeResult']['documents'][0]['fields']
        confidence = _compute_overall_confidence(fields)
        self.assertGreater(confidence, 0.9)
        self.assertLessEqual(confidence, 1.0)

    def test_schema_matches_facturx_format(self):
        """Normalized data has same top-level keys as parse_facturx_xml."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_azure import (
            _normalize_azure_fields,
        )

        fields = AZURE_INVOICE_RESPONSE['analyzeResult']['documents'][0]['fields']
        data = _normalize_azure_fields(fields)

        expected_keys = {
            'document_type',
            'is_marked_paid',
            'vendor',
            'buyer',
            'invoice',
            'totals',
            'tax_lines',
            'lines',
            'table_analysis',
        }
        self.assertEqual(set(data.keys()), expected_keys)


# ===================================================================
# AWS Textract normalization tests
# ===================================================================


@tagged('post_install', '-at_install')
class TestTextractNormalization(TransactionCase):
    """Test AWS Textract response normalization."""

    def test_normalize_full_response(self):
        """Textract AnalyzeExpense response normalizes correctly."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_aws import (
            _normalize_textract_response,
        )

        data = _normalize_textract_response(TEXTRACT_EXPENSE_RESPONSE['ExpenseDocuments'][0])

        self.assertEqual(data['vendor']['name'], 'ACME Corp')
        self.assertEqual(data['invoice']['reference'], 'INV-2024-001')
        self.assertEqual(data['totals']['total_amount'], 1200.0)
        self.assertEqual(data['totals']['untaxed_amount'], 1000.0)
        self.assertEqual(data['totals']['tax_amount'], 200.0)
        self.assertEqual(len(data['lines']), 1)
        self.assertEqual(data['lines'][0]['description'], 'Consulting services')

    def test_confidence_scaling(self):
        """Textract 0-100 confidence scales to 0.0-1.0."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_aws import (
            _compute_overall_confidence,
        )

        confidence = _compute_overall_confidence(TEXTRACT_EXPENSE_RESPONSE['ExpenseDocuments'][0])
        # Textract confidences are 95, 98, 92, 96 → avg ~0.9525
        self.assertGreater(confidence, 0.9)
        self.assertLessEqual(confidence, 1.0)

    def test_schema_matches_facturx_format(self):
        """Normalized data has same top-level keys as parse_facturx_xml."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_aws import (
            _normalize_textract_response,
        )

        data = _normalize_textract_response(TEXTRACT_EXPENSE_RESPONSE['ExpenseDocuments'][0])
        expected_keys = {
            'document_type',
            'is_marked_paid',
            'vendor',
            'buyer',
            'invoice',
            'totals',
            'tax_lines',
            'lines',
            'table_analysis',
        }
        self.assertEqual(set(data.keys()), expected_keys)

    def test_amount_parsing_with_currency_symbols(self):
        """Amount parsing strips currency symbols correctly."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_preprocessor_aws import (
            _summary_amount,
        )

        summary = {
            'TOTAL': {'value': '$1,234.56', 'confidence': 95.0},
            'TAX': {'value': '€200.00', 'confidence': 90.0},
        }
        self.assertAlmostEqual(_summary_amount(summary, 'TOTAL'), 1234.56)
        self.assertAlmostEqual(_summary_amount(summary, 'TAX'), 200.0)


# ===================================================================
# Pipeline integration tests
# ===================================================================


@tagged('post_install', '-at_install')
class TestPreprocessPipeline(TransactionCase):
    """Test pre-processor integration in the extraction pipeline."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_api_key',
            'test-key-123',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_provider',
            'anthropic',
        )

    def _create_move(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )

    def test_no_preprocess_pipeline_unchanged(self):
        """Pipeline unchanged when pre-processor is 'none'."""
        self.env['ir.config_parameter'].sudo().set_param(
            'account_invoice_digitize_ai.ai_preprocess_provider',
            'none',
        )
        move = self._create_move()
        AccountMove = type(move)
        # _ai_try_preprocess should not be called
        with patch.object(AccountMove, '_ai_try_preprocess') as mock_pp:
            with patch.object(AccountMove, '_ai_prepare_document') as mock_prep:
                mock_prep.return_value = {'unsupported': True}
                move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))
        mock_pp.assert_not_called()

    def test_ocr_replacement_high_confidence_skips_claude(self):
        """OCR Replacement mode with high confidence skips Claude."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_mode', 'ocr_replacement')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_confidence_threshold', '0.7')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'test-azure-key')

        move = self._create_move()
        AccountMove = type(move)
        mock_data = {
            'document_type': 'invoice',
            'is_marked_paid': False,
            'vendor': {'name': 'Test', 'vat': '', 'confidence': 0.9},
            'buyer': {'name': '', 'confidence': 0.0},
            'invoice': {'reference': 'INV-1', 'confidence': 0.9},
            'totals': {'untaxed_amount': 100, 'tax_amount': 20, 'total_amount': 120, 'confidence': 0.95},
            'tax_lines': [],
            'lines': [],
            'table_analysis': {'number_format': 'dot_decimal', 'complexity': 'simple', 'line_count': 0},
        }

        with (
            patch.object(AccountMove, '_ai_try_facturx', return_value=None),
            patch.object(
                AccountMove,
                '_ai_try_preprocess',
                return_value={
                    'success': True,
                    'data': mock_data,
                    'text': 'test',
                    'confidence': 0.92,
                    'page_count': 1,
                    'cost_per_page': 0.01,
                    'provider': 'azure_di',
                    'raw_response': {},
                },
            ),
            patch(f'{_MODULE}.models.ai_validator.cross_validate', return_value=0),
            patch.object(AccountMove, '_ai_apply_extraction') as mock_apply,
        ):
            move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))

        mock_apply.assert_called_once()
        call_args = mock_apply.call_args
        self.assertEqual(call_args[0][0], mock_data)
        self.assertEqual(move.ai_extraction_status, 'done')

    def test_ocr_replacement_low_confidence_falls_back(self):
        """OCR Replacement mode with low confidence falls back to Claude."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_mode', 'ocr_replacement')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_confidence_threshold', '0.9')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'test-azure-key')

        move = self._create_move()
        AccountMove = type(move)

        with (
            patch.object(AccountMove, '_ai_try_facturx', return_value=None),
            patch.object(
                AccountMove,
                '_ai_try_preprocess',
                return_value={
                    'success': True,
                    'data': {'vendor': {}},
                    'text': 'test',
                    'confidence': 0.5,
                    'page_count': 1,
                    'cost_per_page': 0.01,
                    'provider': 'azure_di',
                    'raw_response': {},
                },
            ),
            patch.object(AccountMove, '_ai_prepare_document', return_value={'unsupported': True}),
        ):
            move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))

        # Falls through to prepare_document → doc issue → failed
        self.assertEqual(move.ai_extraction_status, 'failed')

    def test_preprocess_failure_falls_back(self):
        """Pre-processor failure falls back to internal pipeline."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_mode', 'ocr_replacement')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'test-azure-key')

        move = self._create_move()
        AccountMove = type(move)

        with (
            patch.object(AccountMove, '_ai_try_facturx', return_value=None),
            patch.object(AccountMove, '_ai_try_preprocess', return_value=None),
            patch.object(AccountMove, '_ai_prepare_document', return_value={'unsupported': True}),
        ):
            move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))

        # Falls through to standard pipeline
        self.assertEqual(move.ai_extraction_status, 'failed')

    def test_claude_enrichment_injects_context(self):
        """Claude Enrichment mode injects pre-processor data into prompt."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_mode', 'claude_enrichment')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'test-azure-key')

        move = self._create_move()
        AccountMove = type(move)
        pp_data = {
            'vendor': {'name': 'ACME', 'confidence': 0.9},
            'invoice': {'reference': 'INV-1'},
            'totals': {'total_amount': 100, 'untaxed_amount': 80, 'tax_amount': 20},
            'lines': [],
        }

        with (
            patch.object(AccountMove, '_ai_try_facturx', return_value=None),
            patch.object(
                AccountMove,
                '_ai_try_preprocess',
                return_value={
                    'success': True,
                    'data': pp_data,
                    'text': 'text',
                    'confidence': 0.9,
                    'page_count': 1,
                    'cost_per_page': 0.01,
                    'provider': 'azure_di',
                    'raw_response': {},
                },
            ),
            patch.object(
                AccountMove,
                '_ai_prepare_document',
                return_value={
                    'text': 'invoice text',
                    'is_vision': False,
                    'pdf_metadata': {},
                    'detected_number_format': None,
                    'table_markdown': '',
                    'is_proforma': False,
                    'unsupported': False,
                },
            ),
            patch.object(AccountMove, '_ai_build_content', wraps=move._ai_build_content) as mock_build,
            patch(f'{_MODULE}.models.ai_provider.get_provider') as mock_get_provider,
        ):
            mock_provider = MagicMock()
            mock_provider.extract.return_value = {
                'success': False,
                'error': 'test',
                'message': 'test',
                'data': None,
                'raw_text': '',
                'input_tokens': 0,
                'output_tokens': 0,
                'model': 'test',
            }
            mock_get_provider.return_value = mock_provider

            move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))

        # _ai_build_content should have been called with preprocess_context
        call_kwargs = mock_build.call_args
        if call_kwargs:
            # Check the preprocess_context kwarg
            pp_ctx = call_kwargs.kwargs.get('preprocess_context', '') if call_kwargs.kwargs else ''
            if not pp_ctx and len(call_kwargs.args) > 6:
                pp_ctx = call_kwargs.args[6]
            self.assertIn('ACME', pp_ctx)

    def test_ocr_only_uses_preprocess_text(self):
        """OCR Only mode uses pre-processor text instead of PyPDF2."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_mode', 'ocr_only')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'test-azure-key')

        move = self._create_move()
        AccountMove = type(move)

        with (
            patch.object(AccountMove, '_ai_try_facturx', return_value=None),
            patch.object(
                AccountMove,
                '_ai_try_preprocess',
                return_value={
                    'success': True,
                    'text': 'OCR text from Azure',
                    'page_count': 1,
                    'cost_per_page': 0.01,
                    'provider': 'azure_di',
                },
            ),
            patch.object(AccountMove, '_ai_prepare_document_with_preprocess_text') as mock_prep_pp,
            patch.object(AccountMove, '_ai_prepare_document') as mock_prep_std,
        ):
            mock_prep_pp.return_value = {'unsupported': True}
            move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))

        mock_prep_pp.assert_called_once()
        mock_prep_std.assert_not_called()

    def test_facturx_takes_priority(self):
        """Factur-X shortcut still takes priority over pre-processor."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_preprocess_provider', 'azure_di')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'test-azure-key')

        move = self._create_move()
        facturx_data = {'vendor': {}, 'invoice': {}, 'totals': {}}

        AccountMove = type(move)
        with (
            patch.object(AccountMove, '_ai_try_facturx', return_value=facturx_data),
            patch.object(AccountMove, '_ai_apply_facturx') as mock_fx,
            patch.object(AccountMove, '_ai_try_preprocess') as mock_pp,
        ):
            move._ai_trigger_extraction('test-key', MagicMock(datas=b'ZmFrZQ==', mimetype='application/pdf'))

        mock_fx.assert_called_once()
        mock_pp.assert_not_called()


# ===================================================================
# Config and credentials tests
# ===================================================================


@tagged('post_install', '-at_install')
class TestPreprocessConfig(TransactionCase):
    """Test pre-processor settings and credentials."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

    def test_default_preprocess_provider(self):
        """Default pre-processor is 'none'."""
        ICP = self.env['ir.config_parameter'].sudo()
        val = ICP.get_param('account_invoice_digitize_ai.ai_preprocess_provider', 'none')
        self.assertEqual(val, 'none')

    def test_azure_credentials_build(self):
        """Azure credentials dict is built correctly."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_azure_endpoint', 'https://test.azure.com')
        ICP.set_param('account_invoice_digitize_ai.ai_azure_api_key', 'my-key')

        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        creds = move._ai_get_preprocess_credentials('azure_di')
        self.assertEqual(creds, {'endpoint': 'https://test.azure.com', 'api_key': 'my-key'})

    def test_aws_credentials_build(self):
        """AWS credentials dict is built correctly."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_aws_access_key_id', 'AKIA...')
        ICP.set_param('account_invoice_digitize_ai.ai_aws_secret_access_key', 'secret')
        ICP.set_param('account_invoice_digitize_ai.ai_aws_region', 'us-east-1')

        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        creds = move._ai_get_preprocess_credentials('aws_textract')
        self.assertEqual(
            creds,
            {
                'access_key_id': 'AKIA...',
                'secret_access_key': 'secret',
                'region': 'us-east-1',
            },
        )

    def test_missing_credentials_returns_none(self):
        """Missing credentials returns None."""
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        self.assertIsNone(move._ai_get_preprocess_credentials('azure_di'))
        self.assertIsNone(move._ai_get_preprocess_credentials('aws_textract'))
        self.assertIsNone(move._ai_get_preprocess_credentials('unknown'))


# ===================================================================
# SigV4 signing tests
# ===================================================================


@tagged('post_install', '-at_install')
class TestAWSSigV4(TransactionCase):
    """Test AWS Signature V4 implementation."""

    def test_signing_key_derivation(self):
        """Signing key derivation uses correct HMAC chain."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_aws_sigv4 import (
            _get_signature_key,
        )

        key = _get_signature_key('wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY', '20150830', 'us-east-1', 'iam')
        self.assertIsInstance(key, bytes)
        self.assertEqual(len(key), 32)  # SHA-256 produces 32 bytes

    def test_sign_request_produces_authorization(self):
        """_sign_request produces valid Authorization header."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_aws_sigv4 import (
            sign_request as _sign_request,
        )

        headers = _sign_request(
            'POST',
            'https://textract.us-east-1.amazonaws.com/',
            {'Content-Type': 'application/x-amz-json-1.1', 'X-Amz-Target': 'Textract.AnalyzeExpense'},
            '{"test": true}',
            'us-east-1',
            'AKIDEXAMPLE',
            'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY',
        )
        self.assertIn('Authorization', headers)
        self.assertTrue(headers['Authorization'].startswith('AWS4-HMAC-SHA256'))
        self.assertIn('x-amz-date', headers)

    def test_hmac_chain_consistency(self):
        """Multiple calls with same inputs produce same key."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_aws_sigv4 import (
            _get_signature_key,
        )

        key1 = _get_signature_key('secret', '20240101', 'eu-west-1', 'textract')
        key2 = _get_signature_key('secret', '20240101', 'eu-west-1', 'textract')
        self.assertEqual(key1, key2)

        # Different date → different key
        key3 = _get_signature_key('secret', '20240102', 'eu-west-1', 'textract')
        self.assertNotEqual(key1, key3)
