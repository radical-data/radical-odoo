import base64
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from .test_extraction import MOCK_CLAUDE_RESPONSE, _make_mock_response


@tagged('post_install', '-at_install')
class TestBatchExtraction(TransactionCase):
    """Test the batch AI extraction wizard."""

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

    def _create_bill_with_pdf(self):
        """Create a draft vendor bill with a fake PDF attachment."""
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

    def test_wizard_creation(self):
        """Wizard should compute counts correctly."""
        move1 = self._create_bill_with_pdf()
        move2 = self._create_bill_with_pdf()
        wizard = self.env['ai.batch.extract.wizard'].create(
            {
                'move_ids': [(6, 0, [move1.id, move2.id])],
            }
        )
        self.assertEqual(wizard.move_count, 2)
        self.assertEqual(wizard.ready_count, 2)
        self.assertEqual(wizard.skip_count, 0)

    def test_skip_no_attachment(self):
        """Invoices without attachments should be skipped."""
        move_no_att = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'company_id': self.company.id,
            }
        )
        move_with_att = self._create_bill_with_pdf()
        wizard = self.env['ai.batch.extract.wizard'].create(
            {
                'move_ids': [(6, 0, [move_no_att.id, move_with_att.id])],
            }
        )
        self.assertEqual(wizard.ready_count, 2)

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_provider.requests.post')
    def test_batch_processing(self, mock_post):
        """Batch extraction should process all ready invoices."""
        mock_post.return_value = _make_mock_response(200, MOCK_CLAUDE_RESPONSE)
        move1 = self._create_bill_with_pdf()
        move2 = self._create_bill_with_pdf()
        wizard = self.env['ai.batch.extract.wizard'].create(
            {
                'move_ids': [(6, 0, [move1.id, move2.id])],
            }
        )
        wizard.action_extract()
        self.assertIn('2 extracted', wizard.result_message)
        self.assertEqual(move1.ai_extraction_status, 'done')
        self.assertEqual(move2.ai_extraction_status, 'done')

    def test_no_api_key_message(self):
        """Without API key, wizard should show an error message."""
        self.env['ir.config_parameter'].sudo().set_param('account_invoice_digitize_ai.ai_api_key', '')
        move = self._create_bill_with_pdf()
        wizard = self.env['ai.batch.extract.wizard'].create(
            {
                'move_ids': [(6, 0, [move.id])],
            }
        )
        wizard.action_extract()
        self.assertIn('No API key', wizard.result_message)
