from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAnomalyDetection(TransactionCase):
    """Test invoice amount anomaly detection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Anomaly Test Vendor',
                'is_company': True,
            }
        )
        # Use a recent date: the detector only looks back LOOKBACK_DAYS (730),
        # so a hard-coded past date silently falls out of the window as time
        # passes and the history lookup returns nothing.
        invoice_date = fields.Date.today()
        # Create 5 posted invoices with amounts around 500
        for amount_str in ['480', '500', '520', '490', '510']:
            bill = cls.env['account.move'].create(
                {
                    'move_type': 'in_invoice',
                    'partner_id': cls.partner.id,
                    'invoice_date': invoice_date,
                    'invoice_line_ids': [
                        (
                            0,
                            0,
                            {
                                'name': 'Test service',
                                'quantity': 1,
                                'price_unit': float(amount_str),
                            },
                        )
                    ],
                }
            )
            bill.action_post()

    def test_anomaly_detected_high(self):
        """Amount 10× higher than average flags anomaly."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_anomaly_detector,
        )

        result = ai_anomaly_detector.detect_anomalies(self.env, self.partner, 5000.0)
        self.assertTrue(result.get('found'))
        self.assertGreater(result['ratio'], 3.0)

    def test_anomaly_detected_low(self):
        """Amount < 10% of average flags anomaly."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_anomaly_detector,
        )

        result = ai_anomaly_detector.detect_anomalies(self.env, self.partner, 5.0)
        self.assertTrue(result.get('found'))
        self.assertLess(result['ratio'], 0.1)

    def test_no_anomaly_normal_amount(self):
        """Amount within normal range should not flag."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_anomaly_detector,
        )

        result = ai_anomaly_detector.detect_anomalies(self.env, self.partner, 550.0)
        self.assertFalse(result.get('found'))

    def test_no_check_insufficient_history(self):
        """Less than 3 invoices → skip check."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_anomaly_detector,
        )

        new_partner = self.env['res.partner'].create(
            {
                'name': 'New Vendor',
                'is_company': True,
            }
        )
        result = ai_anomaly_detector.detect_anomalies(self.env, new_partner, 1000.0)
        self.assertEqual(result, {})

    def test_no_check_without_partner(self):
        """No partner → skip check."""
        from odoo.addons.account_invoice_digitize_ai.models import (
            ai_anomaly_detector,
        )

        result = ai_anomaly_detector.detect_anomalies(self.env, None, 1000.0)
        self.assertEqual(result, {})
