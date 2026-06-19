from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMultiCompany(TransactionCase):
    """Verify company isolation on vendor memory, score, and detectors."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env['res.company'].create({'name': 'Company A'})
        cls.company_b = cls.env['res.company'].create({'name': 'Company B'})
        cls.partner = cls.env['res.partner'].create({'name': 'Vendor Multi', 'vat': 'FR12345678901'})
        # Threshold = 2 for faster auto-apply in tests
        cls.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_auto_apply_threshold', '2')

    # ------------------------------------------------------------------
    # Vendor Memory
    # ------------------------------------------------------------------

    def test_record_correction_sets_company(self):
        """record_correction creates an entry with the given company."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import (
            AiVendorMemory,
        )

        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'AI-001', 'USR-001', company=self.company_a)
        entry = self.env['ai.vendor.memory'].search(
            [('partner_id', '=', self.partner.id), ('company_id', '=', self.company_a.id)]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.company_id, self.company_a)

    def test_vendor_memory_company_isolated(self):
        """Corrections in company A are not returned for company B."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import (
            AiVendorMemory,
        )

        # Record 2 corrections in company A → auto_apply
        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'X', 'Y', company=self.company_a)
        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'X', 'Y', company=self.company_a)

        overrides_a = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner, company=self.company_a)
        overrides_b = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner, company=self.company_b)
        self.assertIn('ref', overrides_a)
        self.assertEqual(overrides_b, {})

    def test_vendor_context_company_scoped(self):
        """get_vendor_context returns only entries for the given company."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import (
            AiVendorMemory,
        )

        # 2 corrections in A (count >= 2 needed for context)
        AiVendorMemory.record_correction(self.env, self.partner, 'account_id', '600', '601', company=self.company_a)
        AiVendorMemory.record_correction(self.env, self.partner, 'account_id', '600', '601', company=self.company_a)

        ctx_a = AiVendorMemory.get_vendor_context(self.env, self.partner, company=self.company_a)
        ctx_b = AiVendorMemory.get_vendor_context(self.env, self.partner, company=self.company_b)
        self.assertIn('601', ctx_a)
        self.assertEqual(ctx_b, '')

    def test_auto_apply_company_scoped(self):
        """Auto-apply overrides are company-specific."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_memory import (
            AiVendorMemory,
        )

        # Auto-apply in A
        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'A1', 'B1', company=self.company_a)
        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'A1', 'B1', company=self.company_a)
        # Single correction in B (below threshold)
        AiVendorMemory.record_correction(self.env, self.partner, 'ref', 'A1', 'B1', company=self.company_b)

        overrides_a = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner, company=self.company_a)
        overrides_b = AiVendorMemory.get_auto_apply_overrides(self.env, self.partner, company=self.company_b)
        self.assertEqual(overrides_a.get('ref'), 'B1')
        self.assertNotIn('ref', overrides_b)

    # ------------------------------------------------------------------
    # Vendor Score
    # ------------------------------------------------------------------

    def test_score_update_sets_company(self):
        """update_score creates a score entry with the given company."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import (
            AiVendorScore,
        )

        AiVendorScore.update_score(self.env, self.partner, had_corrections=False, company=self.company_a)
        score = self.env['ai.vendor.score'].search(
            [('partner_id', '=', self.partner.id), ('company_id', '=', self.company_a.id)]
        )
        self.assertEqual(len(score), 1)
        self.assertEqual(score.total_extractions, 1)

    def test_vendor_score_company_isolated(self):
        """Score in company A does not affect score in company B."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_vendor_score import (
            AiVendorScore,
        )

        AiVendorScore.update_score(self.env, self.partner, had_corrections=False, company=self.company_a)
        AiVendorScore.update_score(self.env, self.partner, had_corrections=True, company=self.company_b)

        score_a = self.env['ai.vendor.score'].search(
            [('partner_id', '=', self.partner.id), ('company_id', '=', self.company_a.id)]
        )
        score_b = self.env['ai.vendor.score'].search(
            [('partner_id', '=', self.partner.id), ('company_id', '=', self.company_b.id)]
        )
        self.assertEqual(score_a.correct_extractions, 1)
        self.assertEqual(score_b.correct_extractions, 0)

    # ------------------------------------------------------------------
    # Anomaly Detector
    # ------------------------------------------------------------------

    def test_anomaly_detector_company_filtered(self):
        """Anomaly detection uses only same-company invoice history."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_anomaly_detector

        journal = self.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', self.company_a.id)],
            limit=1,
        )
        if not journal:
            self.skipTest('No purchase journal for Company A')

        # Create 3 posted invoices in company A (avg = 500)
        for _i in range(3):
            move = self.env['account.move'].create(
                {
                    'move_type': 'in_invoice',
                    'partner_id': self.partner.id,
                    'company_id': self.company_a.id,
                    'journal_id': journal.id,
                    'invoice_date': '2026-01-15',
                    'invoice_line_ids': [(0, 0, {'name': 'Test', 'quantity': 1, 'price_unit': 500.0})],
                }
            )
            move.action_post()

        # Anomaly in company A context: 5000 is 10x avg
        result_a = ai_anomaly_detector.detect_anomalies(self.env, self.partner, 5000.0, company=self.company_a)
        self.assertTrue(result_a.get('found'))

        # Same amount in company B: no history → no anomaly
        result_b = ai_anomaly_detector.detect_anomalies(self.env, self.partner, 5000.0, company=self.company_b)
        self.assertEqual(result_b, {})

    # ------------------------------------------------------------------
    # Duplicate Detector
    # ------------------------------------------------------------------

    def test_duplicate_detector_company_filtered(self):
        """Duplicate detection is scoped to the same company."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_duplicate_detector

        journal_a = self.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', self.company_a.id)],
            limit=1,
        )
        journal_b = self.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', self.company_b.id)],
            limit=1,
        )
        if not journal_a or not journal_b:
            self.skipTest('Missing purchase journal for test companies')

        # Existing invoice in company A
        self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'company_id': self.company_a.id,
                'journal_id': journal_a.id,
                'ref': 'INV-DUP-001',
            }
        )

        # New invoice in company B — should NOT be flagged as duplicate
        move_b = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'company_id': self.company_b.id,
                'journal_id': journal_b.id,
            }
        )
        result = ai_duplicate_detector.detect_duplicates(
            self.env,
            move_b,
            self.partner,
            'INV-DUP-001',
            None,
            None,
            company=self.company_b,
        )
        self.assertFalse(result.get('found'))
