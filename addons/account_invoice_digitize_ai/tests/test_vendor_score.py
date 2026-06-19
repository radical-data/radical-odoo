from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVendorScore(TransactionCase):
    """Test the AI vendor extraction score tracking."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Score Test Vendor',
                'is_company': True,
                'vat': 'FR11111111111',
            }
        )

    # ------------------------------------------------------------------
    # update_score
    # ------------------------------------------------------------------

    def test_update_score_creates_record(self):
        """First extraction should create a score record."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        AiVendorScore.update_score(self.env, self.partner, had_corrections=False)

        score = self.env['ai.vendor.score'].search([('partner_id', '=', self.partner.id)])
        self.assertEqual(len(score), 1)
        self.assertEqual(score.total_extractions, 1)
        self.assertEqual(score.correct_extractions, 1)

    def test_update_score_increments_correct(self):
        """Extraction without corrections should increment correct count."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        AiVendorScore.update_score(self.env, self.partner, had_corrections=False)
        AiVendorScore.update_score(self.env, self.partner, had_corrections=False)

        score = self.env['ai.vendor.score'].search([('partner_id', '=', self.partner.id)])
        self.assertEqual(score.total_extractions, 2)
        self.assertEqual(score.correct_extractions, 2)

    def test_update_score_with_corrections(self):
        """Extraction with corrections should not increment correct count."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        AiVendorScore.update_score(self.env, self.partner, had_corrections=False)
        AiVendorScore.update_score(self.env, self.partner, had_corrections=True)

        score = self.env['ai.vendor.score'].search([('partner_id', '=', self.partner.id)])
        self.assertEqual(score.total_extractions, 2)
        self.assertEqual(score.correct_extractions, 1)

    def test_update_score_saves_last_rate(self):
        """Previous reliability rate should be saved before update."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        AiVendorScore.update_score(self.env, self.partner, had_corrections=False)
        # After first: rate = 100%, last_rate = 0%

        AiVendorScore.update_score(self.env, self.partner, had_corrections=True)
        # After second: rate = 50%, last_rate = 100%

        score = self.env['ai.vendor.score'].search([('partner_id', '=', self.partner.id)])
        self.assertEqual(score.last_reliability_rate, 100.0)
        self.assertEqual(score.reliability_rate, 50.0)

    # ------------------------------------------------------------------
    # Reliability rate computation
    # ------------------------------------------------------------------

    def test_reliability_rate_zero_extractions(self):
        """With zero extractions, reliability rate should be 0."""
        score = self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': 0,
                'correct_extractions': 0,
            }
        )
        self.assertEqual(score.reliability_rate, 0.0)

    def test_reliability_rate_computation(self):
        """Reliability rate should be (correct / total) * 100."""
        score = self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': 10,
                'correct_extractions': 8,
            }
        )
        self.assertAlmostEqual(score.reliability_rate, 80.0, places=1)

    def test_reliability_rate_100_percent(self):
        """Perfect score should be 100%."""
        score = self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': 5,
                'correct_extractions': 5,
            }
        )
        self.assertEqual(score.reliability_rate, 100.0)

    # ------------------------------------------------------------------
    # Degradation detection
    # ------------------------------------------------------------------

    def test_no_degradation_with_few_extractions(self):
        """No degradation warning with fewer than 5 extractions."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        score = self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': 3,
                'correct_extractions': 1,
                'last_reliability_rate': 100.0,
            }
        )
        # Should not raise or log warning (< 5 extractions)
        AiVendorScore._check_degradation(self.env, self.partner, score)

    def test_degradation_detection_triggers(self):
        """Significant drop with enough data should trigger warning."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        score = self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': 10,
                'correct_extractions': 5,
                'last_reliability_rate': 90.0,
            }
        )
        # reliability_rate = 50%, last = 90%, drop = 40% > 20%
        # Should log a warning (we just verify it doesn't crash)
        AiVendorScore._check_degradation(self.env, self.partner, score)

    def test_no_degradation_when_stable(self):
        """Stable rate should not trigger degradation."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import AiVendorScore

        score = self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
                'total_extractions': 10,
                'correct_extractions': 8,
                'last_reliability_rate': 85.0,
            }
        )
        # reliability_rate = 80%, last = 85%, drop = 5% < 20%
        AiVendorScore._check_degradation(self.env, self.partner, score)

    # ------------------------------------------------------------------
    # SQL constraint
    # ------------------------------------------------------------------

    def test_unique_partner_constraint(self):
        """Only one score record per partner should be allowed."""
        self.env['ai.vendor.score'].create(
            {
                'partner_id': self.partner.id,
            }
        )
        with self.assertRaises(Exception):
            self.env['ai.vendor.score'].create(
                {
                    'partner_id': self.partner.id,
                }
            )
