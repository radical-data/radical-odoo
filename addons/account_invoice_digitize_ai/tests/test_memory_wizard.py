import base64
import json

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMemoryWizard(TransactionCase):
    """Tests for vendor memory export/import wizards."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_a = cls.env['res.partner'].create({'name': 'Wizard Vendor A', 'vat': 'FR99999999901'})
        cls.partner_b = cls.env['res.partner'].create({'name': 'Wizard Vendor B', 'vat': 'FR99999999902'})
        cls.company = cls.env.company

        # Create some memory entries
        Memory = cls.env['ai.vendor.memory']
        cls.mem_a = Memory.create(
            {
                'partner_id': cls.partner_a.id,
                'company_id': cls.company.id,
                'field_name': 'account_id',
                'ai_value': '6000',
                'user_value': '6001',
                'correction_count': 5,
                'auto_apply': True,
            }
        )
        cls.mem_b = Memory.create(
            {
                'partner_id': cls.partner_b.id,
                'company_id': cls.company.id,
                'field_name': 'ref',
                'ai_value': 'X',
                'user_value': 'Y',
                'correction_count': 2,
                'auto_apply': False,
            }
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def test_export_all_vendors(self):
        """Export all vendors produces valid JSON with all entries."""
        wizard = self.env['ai.memory.export.wizard'].create({'export_all': True})
        wizard.action_export()

        self.assertTrue(wizard.export_data)
        data = json.loads(base64.b64decode(wizard.export_data))
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 2)

        vats = [e['partner_vat'] for e in data]
        self.assertIn('FR99999999901', vats)
        self.assertIn('FR99999999902', vats)

    def test_export_selected_vendors(self):
        """Export with specific vendor filters correctly."""
        wizard = self.env['ai.memory.export.wizard'].create(
            {
                'export_all': False,
                'partner_ids': [(6, 0, [self.partner_a.id])],
            }
        )
        wizard.action_export()

        data = json.loads(base64.b64decode(wizard.export_data))
        vats = [e['partner_vat'] for e in data]
        self.assertIn('FR99999999901', vats)
        self.assertNotIn('FR99999999902', vats)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def test_import_creates_entries(self):
        """Import creates new memory entries for matching vendors."""
        import_data = [
            {
                'partner_vat': 'FR99999999901',
                'partner_name': 'Wizard Vendor A',
                'field_name': 'tax_ids',
                'ai_value': '20%',
                'user_value': '10%',
                'correction_count': 3,
                'auto_apply': True,
            }
        ]
        file_content = base64.b64encode(json.dumps(import_data).encode('utf-8'))
        wizard = self.env['ai.memory.import.wizard'].create(
            {'import_file': file_content, 'import_filename': 'test.json'}
        )
        wizard.action_import()

        self.assertIn('1 created', wizard.result_message)

        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner_a.id),
                ('field_name', '=', 'tax_ids'),
            ]
        )
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.user_value, '10%')
        self.assertTrue(entry.auto_apply)

    def test_import_updates_existing(self):
        """Import updates existing entries, keeping higher correction count."""
        import_data = [
            {
                'partner_vat': 'FR99999999901',
                'partner_name': 'Wizard Vendor A',
                'field_name': 'account_id',
                'ai_value': '6000',
                'user_value': '6002',
                'correction_count': 10,
                'auto_apply': True,
            }
        ]
        file_content = base64.b64encode(json.dumps(import_data).encode('utf-8'))
        wizard = self.env['ai.memory.import.wizard'].create(
            {'import_file': file_content, 'import_filename': 'test.json'}
        )
        wizard.action_import()

        self.assertIn('1 updated', wizard.result_message)
        self.mem_a.invalidate_recordset()
        self.assertEqual(self.mem_a.user_value, '6002')
        self.assertEqual(self.mem_a.correction_count, 10)

    def test_import_skips_unknown_vendor(self):
        """Vendors not found in the system are skipped."""
        import_data = [
            {
                'partner_vat': 'XX00000000000',
                'partner_name': 'Unknown Corp',
                'field_name': 'ref',
                'ai_value': '',
                'user_value': 'test',
                'correction_count': 1,
            }
        ]
        file_content = base64.b64encode(json.dumps(import_data).encode('utf-8'))
        wizard = self.env['ai.memory.import.wizard'].create(
            {'import_file': file_content, 'import_filename': 'test.json'}
        )
        wizard.action_import()

        self.assertIn('1 skipped', wizard.result_message)

    def test_import_matches_by_vat(self):
        """Vendor matching prefers VAT over name."""
        import_data = [
            {
                'partner_vat': 'FR99999999901',
                'partner_name': 'Completely Different Name',
                'field_name': 'ref',
                'ai_value': 'INV-001',
                'user_value': 'INV-002',
                'correction_count': 1,
            }
        ]
        file_content = base64.b64encode(json.dumps(import_data).encode('utf-8'))
        wizard = self.env['ai.memory.import.wizard'].create(
            {'import_file': file_content, 'import_filename': 'test.json'}
        )
        wizard.action_import()

        self.assertIn('1 created', wizard.result_message)
        entry = self.env['ai.vendor.memory'].search(
            [
                ('partner_id', '=', self.partner_a.id),
                ('field_name', '=', 'ref'),
            ]
        )
        self.assertEqual(len(entry), 1)
