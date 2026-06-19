import logging
import re

from psycopg2 import IntegrityError

from odoo import fields, models

_logger = logging.getLogger(__name__)


def _extract_keywords(text):
    """Extract keywords (>= 3 chars) as lowercase set for description matching."""
    return set(w.lower() for w in re.findall(r'\w+', text or '') if len(w) >= 3)


def _get_auto_apply_threshold(env):
    """Read the auto-apply threshold from ICP.  Returns 0 if learning is disabled."""
    ICP = env['ir.config_parameter'].sudo()
    if not ICP.get_param('account_invoice_digitize_ai.ai_learning_enabled', 'True') == 'True':
        return 0
    try:
        return int(ICP.get_param('account_invoice_digitize_ai.ai_auto_apply_threshold', '3'))
    except (ValueError, TypeError):
        return 3


def _increment_memory(record, user_value, threshold):
    """Increment correction count on an existing memory record.

    Sets ``auto_apply`` when count reaches *threshold*.
    Returns the new correction count.
    """
    new_count = record.correction_count + 1
    vals = {
        'user_value': user_value,
        'correction_count': new_count,
        'last_correction_date': fields.Datetime.now(),
    }
    if new_count >= threshold:
        vals['auto_apply'] = True
    record.write(vals)
    return new_count


class AiVendorMemory(models.Model):
    _name = 'ai.vendor.memory'
    _description = 'AI Vendor Memory'
    _order = 'last_correction_date desc'
    _rec_name = 'field_name'

    partner_id = fields.Many2one(
        'res.partner',
        string='Vendor',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    field_name = fields.Char(
        string='Field',
        required=True,
        help='Odoo field name that was corrected (e.g. account_id, tax_ids).',
    )
    ai_value = fields.Char(
        string='AI Value',
        help='The value originally extracted by AI.',
    )
    user_value = fields.Char(
        required=True,
        help='The value the user corrected it to.',
    )
    correction_count = fields.Integer(
        string='Corrections',
        default=1,
    )
    line_description = fields.Char(
        string='Line Description',
        help='Invoice line description for account-level learning.',
    )
    auto_apply = fields.Boolean(
        string='Auto-apply',
        default=False,
        help='When enabled, this correction is automatically applied to future invoices from this vendor.',
    )
    last_correction_date = fields.Datetime(
        string='Last Correction',
        default=fields.Datetime.now,
    )

    _unique_vendor_company_field_value = models.UniqueIndex(
        '(partner_id, company_id, field_name, ai_value)',
    )
    _partner_company_field_idx = models.Index(
        '(partner_id, company_id, field_name)',
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def record_correction(env, partner, field_name, ai_value, user_value, company=None):
        """Record a user correction for a vendor.

        Creates a new memory entry or increments the existing one.
        Automatically sets ``auto_apply`` when the correction count
        reaches the configured threshold.

        Args:
            env: Odoo environment.
            partner: res.partner recordset (single).
            field_name: Name of the corrected field.
            ai_value: Original value extracted by AI (as string).
            user_value: Value the user corrected it to (as string).
            company: res.company recordset (single), defaults to env.company.
        """
        if not company:
            company = env.company
        threshold = _get_auto_apply_threshold(env)
        if not threshold:
            return
        Memory = env['ai.vendor.memory'].sudo()
        existing = Memory.search(
            [
                ('partner_id', '=', partner.id),
                ('company_id', '=', company.id),
                ('field_name', '=', field_name),
                ('ai_value', '=', ai_value),
            ],
            limit=1,
        )

        if existing:
            new_count = _increment_memory(existing, user_value, threshold)
            _logger.info(
                'Vendor memory updated: %s / %s (%d corrections)',
                partner.name,
                field_name,
                new_count,
            )
        else:
            try:
                with env.cr.savepoint():
                    Memory.create(
                        {
                            'partner_id': partner.id,
                            'company_id': company.id,
                            'field_name': field_name,
                            'ai_value': ai_value,
                            'user_value': user_value,
                            'correction_count': 1,
                            'auto_apply': 1 >= threshold,
                            'last_correction_date': fields.Datetime.now(),
                        }
                    )
                _logger.info(
                    'Vendor memory created: %s / %s',
                    partner.name,
                    field_name,
                )
            except IntegrityError:
                # Race condition: another request created the same record
                # Savepoint rollback preserves the outer transaction
                existing = Memory.search(
                    [
                        ('partner_id', '=', partner.id),
                        ('company_id', '=', company.id),
                        ('field_name', '=', field_name),
                        ('ai_value', '=', ai_value),
                    ],
                    limit=1,
                )
                if existing:
                    _increment_memory(existing, user_value, threshold)
                _logger.info(
                    'Vendor memory race resolved: %s / %s',
                    partner.name,
                    field_name,
                )

    @staticmethod
    def get_vendor_context(env, partner, company=None):
        """Return a text block describing past corrections for this vendor.

        Used to inject into the Claude prompt so the AI is aware of
        previously corrected preferences.

        Args:
            env: Odoo environment.
            partner: res.partner recordset (single).
            company: res.company recordset (single), defaults to env.company.

        Returns:
            str: Prompt context block, or empty string if no memory.
        """
        if not company:
            company = env.company
        Memory = env['ai.vendor.memory']
        entries = Memory.search(
            [
                ('partner_id', '=', partner.id),
                ('company_id', '=', company.id),
                ('correction_count', '>=', 2),
            ],
            order='correction_count desc',
            limit=20,
        )

        if not entries:
            return ''

        lines = ['Past corrections for this vendor (apply these preferences):']
        for entry in entries:
            auto = ' [AUTO-APPLY]' if entry.auto_apply else ''
            lines.append(
                "- Field '%s': AI suggested '%s' → user corrected to '%s' "
                '(%d times)%s'
                % (entry.field_name, entry.ai_value or '(empty)', entry.user_value, entry.correction_count, auto)
            )
        return '\n'.join(lines)

    @staticmethod
    def get_auto_apply_overrides(env, partner, company=None):
        """Return a dict of auto-apply overrides for this vendor.

        Only returns entries where ``auto_apply`` is True.

        Args:
            env: Odoo environment.
            partner: res.partner recordset (single).
            company: res.company recordset (single), defaults to env.company.

        Returns:
            dict: Mapping of ``{field_name: user_value}``.
                  If multiple entries exist for the same field, the most
                  recently corrected one wins.
        """
        if not company:
            company = env.company
        Memory = env['ai.vendor.memory']
        entries = Memory.search(
            [
                ('partner_id', '=', partner.id),
                ('company_id', '=', company.id),
                ('auto_apply', '=', True),
            ],
            order='last_correction_date desc',
        )

        overrides = {}
        for entry in entries:
            if entry.field_name not in overrides:
                overrides[entry.field_name] = entry.user_value
        return overrides

    @staticmethod
    def get_account_override(env, partner, description, company=None):
        """Return the auto-apply account ID for a vendor + line description.

        Searches vendor memory entries with ``field_name='account_id'`` and
        ``auto_apply=True``. Uses keyword overlap (>= 50%) to match the
        description — same heuristic as ``match_account`` tier 1.

        Args:
            env: Odoo environment.
            partner: res.partner recordset (single).
            description: Invoice line description string.
            company: res.company recordset (single), defaults to env.company.

        Returns:
            int or None: ``account.account`` record ID, or ``None``.
        """
        if not partner or not description:
            return None
        if not company:
            company = env.company

        Memory = env['ai.vendor.memory']
        entries = Memory.search(
            [
                ('partner_id', '=', partner.id),
                ('company_id', '=', company.id),
                ('field_name', '=', 'account_id'),
                ('auto_apply', '=', True),
                ('line_description', '!=', False),
            ],
            order='correction_count desc',
        )
        if not entries:
            return None

        desc_words = _extract_keywords(description)
        if not desc_words:
            return None

        for entry in entries:
            entry_words = _extract_keywords(entry.line_description)
            if not entry_words:
                continue
            overlap = len(desc_words & entry_words)
            threshold = min(len(desc_words), len(entry_words)) * 0.5
            if overlap >= threshold:
                try:
                    account_id = int(entry.user_value)
                    account = env['account.account'].browse(account_id).exists()
                    if account:
                        return account_id
                except (ValueError, TypeError):
                    continue
        return None

    @staticmethod
    def record_line_correction(env, partner, description, ai_account_id, user_account_id, company=None):
        """Record a line-level account correction for learning.

        Args:
            env: Odoo environment.
            partner: res.partner recordset (single).
            description: Invoice line description.
            ai_account_id: Original account ID from AI (as string).
            user_account_id: Account ID the user corrected to (as string).
            company: res.company recordset (single), defaults to env.company.
        """
        if not company:
            company = env.company
        threshold = _get_auto_apply_threshold(env)
        if not threshold:
            return
        Memory = env['ai.vendor.memory'].sudo()
        existing = Memory.search(
            [
                ('partner_id', '=', partner.id),
                ('company_id', '=', company.id),
                ('field_name', '=', 'account_id'),
                ('ai_value', '=', str(ai_account_id)),
                ('line_description', '!=', False),
            ],
            order='correction_count desc, id desc',
            limit=50,
        )
        # Find a matching description entry
        desc_words = _extract_keywords(description)
        matched = None
        if desc_words:
            for entry in existing:
                entry_words = _extract_keywords(entry.line_description)
                if not entry_words:
                    continue
                overlap = len(desc_words & entry_words)
                if overlap >= min(len(desc_words), len(entry_words)) * 0.5:
                    matched = entry
                    break

        if matched:
            _increment_memory(matched, str(user_account_id), threshold)
        else:
            try:
                with env.cr.savepoint():
                    Memory.create(
                        {
                            'partner_id': partner.id,
                            'company_id': company.id,
                            'field_name': 'account_id',
                            'ai_value': str(ai_account_id),
                            'user_value': str(user_account_id),
                            'line_description': description or '',
                            'correction_count': 1,
                            'auto_apply': 1 >= threshold,
                            'last_correction_date': fields.Datetime.now(),
                        }
                    )
            except IntegrityError:
                pass  # Savepoint rollback preserves outer transaction
