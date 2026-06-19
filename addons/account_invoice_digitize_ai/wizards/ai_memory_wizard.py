import base64
import json
import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiMemoryExportWizard(models.TransientModel):
    _name = 'ai.memory.export.wizard'
    _description = 'Export Vendor Memory'

    partner_ids = fields.Many2many('res.partner', string='Vendors')
    export_all = fields.Boolean(string='Export All Vendors', default=True)
    export_data = fields.Binary(string='Download', readonly=True)
    export_filename = fields.Char()

    def action_export(self):
        """Export vendor memory entries to a JSON file."""
        self.ensure_one()
        domain = [('company_id', '=', self.env.company.id)]
        if not self.export_all and self.partner_ids:
            domain.append(('partner_id', 'in', self.partner_ids.ids))

        entries = self.env['ai.vendor.memory'].search(domain)
        data = [
            {
                'partner_vat': e.partner_id.vat or '',
                'partner_name': e.partner_id.name,
                'field_name': e.field_name,
                'ai_value': e.ai_value or '',
                'user_value': e.user_value,
                'correction_count': e.correction_count,
                'auto_apply': e.auto_apply,
            }
            for e in entries
        ]

        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
        self.export_data = base64.b64encode(json_bytes)
        self.export_filename = 'vendor_memory_%s.json' % fields.Date.today()

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class AiMemoryImportWizard(models.TransientModel):
    _name = 'ai.memory.import.wizard'
    _description = 'Import Vendor Memory'

    import_file = fields.Binary(string='JSON File', required=True)
    import_filename = fields.Char()
    result_message = fields.Text(string='Result', readonly=True)

    _VALID_IMPORT_FIELDS = {
        'partner_id',
        'ref',
        'invoice_date',
        'invoice_date_due',
        'account_id',
        'tax_ids',
        'line_description',
    }

    def _import_match_partner(self, entry):
        """Match a partner by VAT first, then by name.  Returns partner or falsy."""
        partner = None
        if entry.get('partner_vat'):
            partner = self.env['res.partner'].search(
                [('vat', '=', entry['partner_vat'])],
                limit=1,
            )
        if not partner and entry.get('partner_name'):
            partner = self.env['res.partner'].search(
                [('name', '=ilike', entry['partner_name'])],
                limit=1,
            )
        return partner

    def action_import(self):
        """Import vendor memory entries from a JSON file."""
        self.ensure_one()
        raw = base64.b64decode(self.import_file)
        if len(raw) > 5_000_000:  # 5 MB safety limit
            raise UserError(self.env._('Import file is too large (max 5 MB).'))
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise UserError(self.env._('Invalid JSON file.'))
        if not isinstance(data, list):
            raise UserError(self.env._('Invalid JSON file: expected a JSON array.'))

        created = updated = skipped = 0
        Memory = self.env['ai.vendor.memory']
        company = self.env.company
        create_buffer = []

        for entry in data:
            field_name = entry.get('field_name')
            user_value = entry.get('user_value')
            if not field_name or user_value is None or field_name not in self._VALID_IMPORT_FIELDS:
                skipped += 1
                continue

            partner = self._import_match_partner(entry)
            if not partner:
                skipped += 1
                continue

            existing = Memory.search(
                [
                    ('partner_id', '=', partner.id),
                    ('company_id', '=', company.id),
                    ('field_name', '=', field_name),
                    ('ai_value', '=', entry.get('ai_value', '')),
                ],
                limit=1,
            )

            if existing:
                existing.write(
                    {
                        'user_value': user_value,
                        'correction_count': max(
                            existing.correction_count,
                            entry.get('correction_count', 1),
                        ),
                        'auto_apply': entry.get('auto_apply', False),
                    }
                )
                updated += 1
            else:
                create_buffer.append(
                    {
                        'partner_id': partner.id,
                        'company_id': company.id,
                        'field_name': field_name,
                        'ai_value': entry.get('ai_value', ''),
                        'user_value': user_value,
                        'correction_count': entry.get('correction_count', 1),
                        'auto_apply': entry.get('auto_apply', False),
                    }
                )
                created += 1

        if create_buffer:
            Memory.create(create_buffer)

        self.result_message = self.env._('Import complete: %d created, %d updated, %d skipped (vendor not found)') % (
            created,
            updated,
            skipped,
        )

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
