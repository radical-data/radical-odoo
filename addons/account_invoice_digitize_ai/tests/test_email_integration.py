from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestEmailIntegration(TransactionCase):
    """Test email-to-vendor-bill creation and auto-extraction."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {
                'name': 'Test Vendor',
                'email': 'vendor@example.com',
            }
        )

    def _make_msg_dict(self, email_from='vendor@example.com', subject='Invoice 2024-001'):
        """Build a minimal msg_dict like Odoo's mail gateway produces."""
        return {
            'email_from': email_from,
            'from': email_from,
            'subject': subject,
            'body': '<p>Please find attached invoice.</p>',
            'message_type': 'email',
            'subtype_id': self.env.ref('mail.mt_comment').id,
        }

    # -----------------------------------------------------------------
    # message_new() basics
    # -----------------------------------------------------------------

    def test_message_new_creates_vendor_bill(self):
        """message_new() creates an account.move with move_type=in_invoice."""
        msg = self._make_msg_dict()
        move = self.env['account.move'].message_new(msg)
        self.assertEqual(move.move_type, 'in_invoice')

    def test_message_new_partner_from_email(self):
        """Sender email matches a partner -> partner_id is set."""
        msg = self._make_msg_dict(email_from='vendor@example.com')
        move = self.env['account.move'].message_new(msg)
        self.assertEqual(move.partner_id, self.partner)

    def test_message_new_unknown_email(self):
        """Unknown sender -> partner_id not set."""
        msg = self._make_msg_dict(email_from='unknown@nowhere.com')
        move = self.env['account.move'].message_new(msg)
        self.assertFalse(move.partner_id)

    def test_message_new_custom_values_preserved(self):
        """Explicit custom_values are not overridden by the email logic."""
        other_partner = self.env['res.partner'].create({'name': 'Other', 'email': 'other@x.com'})
        msg = self._make_msg_dict(email_from='vendor@example.com')
        move = self.env['account.move'].message_new(
            msg,
            custom_values={
                'partner_id': other_partner.id,
            },
        )
        self.assertEqual(move.partner_id, other_partner)

    # -----------------------------------------------------------------
    # Auto-extraction toggle
    # -----------------------------------------------------------------

    def test_auto_extract_disabled_by_default(self):
        """With auto-extract off, no extraction is triggered."""
        msg = self._make_msg_dict()
        with patch.object(type(self.env['account.move']), '_ai_trigger_extraction') as mock_extract:
            self.env['account.move'].message_new(msg)
            mock_extract.assert_not_called()

    def test_auto_extract_enabled(self):
        """With auto-extract on + API key + attachment, extraction fires."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_email_auto_extract', 'True')
        ICP.set_param('account_invoice_digitize_ai.ai_api_key', 'sk-test-key')

        AccountMoveClass = type(self.env['account.move'])
        with (
            patch.object(AccountMoveClass, '_ai_trigger_extraction') as mock_extract,
            patch.object(AccountMoveClass, '_ai_get_invoice_attachment', return_value=MagicMock(id=1)),
        ):
            msg = self._make_msg_dict()
            self.env['account.move'].message_new(msg)
            mock_extract.assert_called_once()

    def test_auto_extract_no_api_key(self):
        """With auto-extract on but no API key, no crash."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_email_auto_extract', 'True')
        ICP.set_param('account_invoice_digitize_ai.ai_api_key', '')

        msg = self._make_msg_dict()
        move = self.env['account.move'].message_new(msg)
        self.assertTrue(move.id)

    def test_auto_extract_exception_handled(self):
        """Extraction raises exception -> bill still created, no crash."""
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('account_invoice_digitize_ai.ai_email_auto_extract', 'True')
        ICP.set_param('account_invoice_digitize_ai.ai_api_key', 'sk-test-key')

        AccountMoveClass = type(self.env['account.move'])
        with (
            patch.object(AccountMoveClass, '_ai_trigger_extraction', side_effect=Exception('API timeout')),
            patch.object(AccountMoveClass, '_ai_get_invoice_attachment', return_value=MagicMock(id=1)),
        ):
            msg = self._make_msg_dict()
            move = self.env['account.move'].message_new(msg)
            self.assertTrue(move.id)
            self.assertEqual(move.move_type, 'in_invoice')
