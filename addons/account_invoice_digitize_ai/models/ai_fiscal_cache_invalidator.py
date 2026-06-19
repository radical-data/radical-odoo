"""Invalidate fiscal context cache when accounts or taxes change.

The fiscal cache (built daily in ai_fiscal_context.py) stores expense
accounts and purchase taxes.  Without invalidation, newly created
accounts or taxes would not appear in AI prompts until the next day.
"""

from odoo import api, models

from .ai_fiscal_context import invalidate_fiscal_cache

_ACCOUNT_CACHE_FIELDS = {'code', 'name', 'active', 'deprecated', 'account_type'}
_TAX_CACHE_FIELDS = {'name', 'amount', 'type_tax_use', 'active'}


class AccountAccount(models.Model):
    _inherit = 'account.account'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        invalidate_fiscal_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        if _ACCOUNT_CACHE_FIELDS & set(vals):
            invalidate_fiscal_cache()
        return res


class AccountTax(models.Model):
    _inherit = 'account.tax'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        invalidate_fiscal_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        if _TAX_CACHE_FIELDS & set(vals):
            invalidate_fiscal_cache()
        return res
