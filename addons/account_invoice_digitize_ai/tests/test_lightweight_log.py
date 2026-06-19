"""Tests for always-on lightweight extraction log."""

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLightweightLog(TransactionCase):
    """Test that extraction logs are always created with lightweight fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls._p = 'account_invoice_digitize_ai.'

        cls.purchase_journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

        # Mock API result
        cls.mock_result = {
            'success': True,
            'data': {
                'vendor': {'name': 'Test Vendor SA', 'confidence': 0.9},
                'invoice': {'reference': 'INV-001', 'invoice_date': '2026-01-15'},
                'totals': {
                    'total_amount': 1200.00,
                    'untaxed_amount': 1000.00,
                    'tax_amount': 200.00,
                    'confidence': 0.85,
                },
            },
            'raw_text': '{"vendor": {"name": "Test Vendor SA"}}',
            'input_tokens': 500,
            'output_tokens': 200,
            'model': 'claude-haiku-4-5-20251001',
        }

    def _create_invoice(self):
        return self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'journal_id': self.purchase_journal.id,
            }
        )

    def _mock_provider(self):
        """Return a mock provider that returns cls.mock_result."""
        provider = MagicMock()
        provider.extract.return_value = self.mock_result
        provider.estimate_cost.return_value = 0.001
        return provider

    def test_log_created_without_debug(self):
        """Log should be created even when debug mode is OFF."""
        move = self._create_invoice()
        provider = self._mock_provider()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=False,
                start_time=None,
                mode='text',
            )

        self.assertTrue(move.ai_extraction_log_id)
        log = move.ai_extraction_log_id
        self.assertTrue(log.success)
        self.assertEqual(log.extraction_mode, 'text')

    def test_log_no_prompt_without_debug(self):
        """prompt_sent should be empty when debug mode is OFF."""
        move = self._create_invoice()
        provider = self._mock_provider()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=False,
                start_time=None,
                mode='text',
            )

        log = move.ai_extraction_log_id
        self.assertFalse(log.prompt_sent)
        self.assertFalse(log.response_received)

    def test_log_has_prompt_with_debug(self):
        """prompt_sent should be populated when debug mode is ON."""
        move = self._create_invoice()
        provider = self._mock_provider()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=True,
                start_time=None,
                mode='text',
            )

        log = move.ai_extraction_log_id
        self.assertEqual(log.prompt_sent, 'test prompt')
        self.assertTrue(log.response_received)

    def test_log_vendor_name_populated(self):
        """vendor_name should be extracted from result data."""
        move = self._create_invoice()
        provider = self._mock_provider()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=False,
                start_time=None,
                mode='text',
            )

        log = move.ai_extraction_log_id
        self.assertEqual(log.vendor_name, 'Test Vendor SA')

    def test_log_overall_confidence(self):
        """overall_confidence should be extracted from totals."""
        move = self._create_invoice()
        provider = self._mock_provider()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=False,
                start_time=None,
                mode='text',
            )

        log = move.ai_extraction_log_id
        self.assertAlmostEqual(log.overall_confidence, 0.85, places=2)

    def test_log_provider_name(self):
        """provider_name should be stored."""
        move = self._create_invoice()
        provider = self._mock_provider()

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=False,
                start_time=None,
                mode='vision',
            )

        log = move.ai_extraction_log_id
        self.assertEqual(log.provider_name, 'anthropic')
        self.assertEqual(log.extraction_mode, 'vision')

    def test_log_duration_with_start_time(self):
        """duration_seconds should be positive when start_time is provided."""
        import time

        move = self._create_invoice()
        provider = self._mock_provider()
        start = time.time() - 2.5  # 2.5 seconds ago

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_provider.get_provider',
            return_value=provider,
        ):
            move._ai_create_log(
                'test prompt',
                self.mock_result,
                provider_name='anthropic',
                model_id='claude-haiku-4-5-20251001',
                debug_mode=False,
                start_time=start,
                mode='text',
            )

        log = move.ai_extraction_log_id
        self.assertGreater(log.duration_seconds, 2.0)
