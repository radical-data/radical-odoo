from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestConfig(TransactionCase):
    """Test settings fields and configuration storage."""

    def test_default_provider(self):
        """Default AI provider should be 'anthropic'."""
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.ai_provider, 'anthropic')

    def test_default_model(self):
        """Default model should be Claude Haiku."""
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.ai_model_selection, 'claude-haiku-4-5-20251001')

    def test_default_extract_lines(self):
        """Line extraction should be disabled by default."""
        settings = self.env['res.config.settings'].create({})
        self.assertFalse(settings.ai_extract_lines)

    def test_default_debug_mode(self):
        """Debug mode should be disabled by default."""
        settings = self.env['res.config.settings'].create({})
        self.assertFalse(settings.ai_debug_mode)

    def test_default_auto_apply_threshold(self):
        """Auto-apply threshold should default to 3."""
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.ai_auto_apply_threshold, 3)

    def test_api_key_stored_in_config_parameter(self):
        """API key should be persisted via ir.config_parameter."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_api_key', 'test-key-123')
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.ai_api_key, 'test-key-123')

    def test_provider_selection_values(self):
        """Active providers should be available as selection options."""
        field = self.env['res.config.settings']._fields['ai_provider']
        keys = [k for k, _v in field.selection]
        self.assertIn('anthropic', keys)
        self.assertIn('openai', keys)
        self.assertIn('google', keys)
        self.assertIn('xai', keys)

    def test_model_selection_values(self):
        """Claude models should be available (dynamic selection from providers)."""
        Model = self.env['res.config.settings']
        keys = [k for k, _v in Model._selection_ai_model()]
        self.assertIn('claude-haiku-4-5-20251001', keys)
        self.assertIn('claude-sonnet-4-6', keys)
        self.assertIn('claude-opus-4-6', keys)

    def test_cost_estimate_computed(self):
        """Cost estimate should return a non-empty string."""
        settings = self.env['res.config.settings'].create(
            {
                'ai_provider': 'anthropic',
                'ai_model_selection': 'claude-haiku-4-5-20251001',
            }
        )
        self.assertTrue(settings.ai_cost_estimate)
        self.assertIn('$', settings.ai_cost_estimate)

    def test_cost_estimate_changes_with_model(self):
        """Opus should be more expensive than Haiku."""
        settings_haiku = self.env['res.config.settings'].create(
            {
                'ai_model_selection': 'claude-haiku-4-5-20251001',
            }
        )
        settings_opus = self.env['res.config.settings'].create(
            {
                'ai_model_selection': 'claude-opus-4-6',
            }
        )
        # Both should have estimates
        self.assertTrue(settings_haiku.ai_cost_estimate)
        self.assertTrue(settings_opus.ai_cost_estimate)

    def test_rounding_strategy_selection_values(self):
        """Rounding strategy should have adjust and line options."""
        field = self.env['res.config.settings']._fields['ai_rounding_strategy']
        keys = [k for k, _v in field.selection]
        self.assertIn('adjust', keys)
        self.assertIn('line', keys)

    def test_rounding_tolerance_default(self):
        """Rounding tolerance should default to 0.05."""
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.ai_rounding_tolerance, 0.05)
