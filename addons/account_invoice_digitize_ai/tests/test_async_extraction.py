"""Tests for asynchronous (cron-based) extraction."""

import base64
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAsyncExtraction(TransactionCase):
    """Test background extraction via ir.cron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls._p = 'account_invoice_digitize_ai.'

        cls.ICP.set_param(cls._p + 'ai_api_key', 'test-key')
        cls.ICP.set_param(cls._p + 'ai_provider', 'anthropic')
        cls.ICP.set_param(cls._p + 'ai_debug_mode', 'False')

        cls.purchase_journal = cls.env['account.journal'].search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)],
            limit=1,
        )

        cls.mock_data = {
            'vendor': {'name': 'Async Test Vendor', 'confidence': 0.9},
            'invoice': {'reference': 'ASYNC-001', 'invoice_date': '2026-01-15'},
            'totals': {
                'total_amount': 1200.00,
                'untaxed_amount': 1000.00,
                'tax_amount': 200.00,
                'confidence': 0.85,
            },
        }

    def _create_invoice_with_attachment(self):
        move = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'journal_id': self.purchase_journal.id,
            }
        )
        self.env['ir.attachment'].create(
            {
                'name': 'test_invoice.pdf',
                'datas': base64.b64encode(b'%PDF-fake'),
                'res_model': 'account.move',
                'res_id': move.id,
                'mimetype': 'application/pdf',
            }
        )
        return move

    def test_button_queues_extraction(self):
        """Button should set status='processing' + queued_at when async enabled."""
        self.ICP.set_param(self._p + 'ai_async_extraction', 'True')
        move = self._create_invoice_with_attachment()

        result = move.action_ai_extract()

        self.assertEqual(move.ai_extraction_status, 'processing')
        self.assertTrue(move.ai_extraction_queued_at)
        self.assertEqual(result['tag'], 'display_notification')

    def test_cron_processes_queue(self):
        """Cron should extract and cache data for queued moves."""
        self.ICP.set_param(self._p + 'ai_async_extraction', 'True')
        move = self._create_invoice_with_attachment()
        move.action_ai_extract()

        with (
            patch.object(
                type(move),
                '_ai_trigger_extraction',
                return_value=self.mock_data,
            ),
            patch.object(self.env.cr, 'commit'),
        ):
            self.env['account.move']._ai_cron_process_queue()

        self.assertEqual(move.ai_extraction_status, 'done')
        self.assertTrue(move.ai_last_extraction_data)
        self.assertFalse(move.ai_extraction_queued_at)

    def test_cron_handles_failure(self):
        """Cron should set status='failed' when extraction returns None."""
        self.ICP.set_param(self._p + 'ai_async_extraction', 'True')
        move = self._create_invoice_with_attachment()
        move.action_ai_extract()

        with (
            patch.object(
                type(move),
                '_ai_trigger_extraction',
                return_value=None,
            ),
            patch.object(self.env.cr, 'commit'),
        ):
            self.env['account.move']._ai_cron_process_queue()

        self.assertEqual(move.ai_extraction_status, 'failed')
        self.assertFalse(move.ai_extraction_queued_at)

    def test_sync_fallback(self):
        """When async is disabled, extraction should happen synchronously."""
        self.ICP.set_param(self._p + 'ai_async_extraction', 'False')
        move = self._create_invoice_with_attachment()

        with patch.object(
            type(move),
            '_ai_trigger_extraction',
            return_value=self.mock_data,
        ):
            result = move.action_ai_extract()

        # Should open preview wizard directly (not a notification)
        self.assertEqual(result.get('res_model'), 'ai.preview.wizard')
        self.assertFalse(move.ai_extraction_queued_at)

    def test_view_results_opens_preview(self):
        """action_ai_view_results should open wizard from cached data."""
        move = self._create_invoice_with_attachment()
        move.ai_last_extraction_data = json.dumps(self.mock_data)

        result = move.action_ai_view_results()

        self.assertEqual(result.get('res_model'), 'ai.preview.wizard')

    def test_cron_skips_without_api_key(self):
        """Cron should skip processing when no API key is configured."""
        self.ICP.set_param(self._p + 'ai_api_key', '')
        self.ICP.set_param(self._p + 'ai_async_extraction', 'True')
        move = self._create_invoice_with_attachment()
        move.ai_extraction_status = 'processing'
        move.ai_extraction_queued_at = fields.Datetime.now()

        self.env['account.move']._ai_cron_process_queue()

        # Status should remain unchanged (not processed, not stale)
        self.assertEqual(move.ai_extraction_status, 'processing')

    def test_cron_marks_stale_as_failed(self):
        """Items queued > 10 minutes ago should be marked as failed."""
        self.ICP.set_param(self._p + 'ai_async_extraction', 'True')
        move = self._create_invoice_with_attachment()
        move.ai_extraction_status = 'processing'
        move.ai_extraction_queued_at = fields.Datetime.now() - timedelta(minutes=15)

        with patch.object(self.env.cr, 'commit'):
            self.env['account.move']._ai_cron_process_queue()

        self.assertEqual(move.ai_extraction_status, 'failed')
        self.assertFalse(move.ai_extraction_queued_at)

    def test_cron_batch_size_limit(self):
        """Cron should only process up to 5 items per run."""
        self.ICP.set_param(self._p + 'ai_async_extraction', 'True')
        moves = []
        for _i in range(7):
            m = self._create_invoice_with_attachment()
            m.ai_extraction_status = 'processing'
            m.ai_extraction_queued_at = fields.Datetime.now()
            moves.append(m)

        with (
            patch.object(
                type(moves[0]),
                '_ai_trigger_extraction',
                return_value=self.mock_data,
            ),
            patch.object(self.env.cr, 'commit'),
        ):
            self.env['account.move']._ai_cron_process_queue()

        done = [m for m in moves if m.ai_extraction_status == 'done']
        still_queued = [m for m in moves if m.ai_extraction_status == 'processing']
        self.assertEqual(len(done), 5)
        self.assertEqual(len(still_queued), 2)

    def test_re_extract_clears_cache(self):
        """action_ai_re_extract should clear cached data and re-trigger."""
        move = self._create_invoice_with_attachment()
        move.ai_last_extraction_data = json.dumps({'old': True})
        move.ai_extraction_status = 'done'

        call_count = []

        def mock_extract(api_key, attachment, preview=False):
            call_count.append(1)
            return self.mock_data

        with patch.object(
            type(move),
            '_ai_trigger_extraction',
            side_effect=mock_extract,
        ):
            move.action_ai_re_extract()

        # A fresh extraction should have been triggered (not reused from cache)
        self.assertEqual(len(call_count), 1)
        # Data should be re-populated with new extraction result
        data = json.loads(move.ai_last_extraction_data)
        self.assertNotIn('old', data)
