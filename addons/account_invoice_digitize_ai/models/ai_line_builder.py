import json
import logging

from odoo import fields, models

from . import ai_matcher
from .ai_vendor_memory import AiVendorMemory

_logger = logging.getLogger(__name__)


def _resolve_line_qty_price(line_data):
    """Extract quantity and unit price from line data, handling TTC back-calc."""
    raw_qty = line_data.get('quantity')
    quantity = raw_qty if raw_qty is not None else 1.0

    raw_price = line_data.get('unit_price')
    if raw_price is None:
        raw_price = line_data.get('subtotal_untaxed')
    price_unit = raw_price if raw_price is not None else 0.0

    # If unit_price is TTC and we have a tax rate, back-calculate HT
    tax_rate = line_data.get('tax_rate') or 0
    if line_data.get('unit_price_is_tax_included') and tax_rate and tax_rate != -100:
        price_unit = round(price_unit / (1 + tax_rate / 100), 2)

    return quantity, price_unit


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ===================================================================
    # Correction detection (learning)
    # ===================================================================

    def write(self, vals):
        """Override to detect user corrections to AI-filled fields."""
        # Fast path: skip ICP query when no AI-tracked fields are touched
        has_tracked = self._AI_TRACKED_FIELDS & set(vals.keys())
        has_lines = 'invoice_line_ids' in vals
        if not has_tracked and not has_lines:
            return super().write(vals)

        ICP = self.env['ir.config_parameter'].sudo()
        mode = ICP.get_param('account_invoice_digitize_ai.ai_extraction_mode', 'guided')

        corrections = self._ai_collect_header_corrections(vals, mode)
        line_snapshots = self._ai_collect_line_snapshots(vals, mode)

        res = super().write(vals)

        for rec, partner, field_name, ai_val, user_val in corrections:
            company = rec.company_id or self.env.company
            AiVendorMemory.record_correction(self.env, partner, field_name, ai_val, user_val, company=company)
        self._ai_detect_line_corrections(line_snapshots)

        return res

    def _ai_collect_header_corrections(self, vals, mode):
        """Collect header-level corrections before write (for learning)."""
        corrections = []
        changed_tracked = self._AI_TRACKED_FIELDS & set(vals.keys())
        if mode != 'guided' or not changed_tracked or 'ai_extracted_values' in vals:
            return corrections
        for rec in self:
            if not rec.ai_extracted_values or not rec.partner_id:
                continue
            if rec._ai_is_customer_invoice():
                continue
            try:
                snapshot = json.loads(rec.ai_extracted_values)
            except (json.JSONDecodeError, TypeError):
                continue
            for field_name in changed_tracked:
                ai_val = snapshot.get(field_name, '')
                new_val = str(vals[field_name]) if vals[field_name] is not None else ''
                if ai_val != new_val:
                    corrections.append((rec, rec.partner_id, field_name, ai_val, new_val))
        return corrections

    def _ai_collect_line_snapshots(self, vals, mode):
        """Collect line snapshots before write (for account learning)."""
        if mode != 'guided' or 'invoice_line_ids' not in vals or 'ai_extracted_values' in vals:
            return {}
        line_snapshots = {}
        for rec in self:
            if not rec.ai_extracted_values or not rec.partner_id:
                continue
            if rec._ai_is_customer_invoice():
                continue
            try:
                snap = json.loads(rec.ai_extracted_values)
            except (json.JSONDecodeError, TypeError):
                continue
            if snap.get('_lines'):
                line_snapshots[rec.id] = (rec, snap['_lines'])
        return line_snapshots

    def _ai_detect_line_corrections(self, line_snapshots):
        """Detect account_id corrections on invoice lines vs AI snapshot."""
        for _move_id, (rec, ai_lines) in line_snapshots.items():
            if not rec.exists() or not rec.partner_id:
                continue
            company = rec.company_id or self.env.company
            # Build lookup: description → ai_account_id
            ai_map = {}
            for ai_line in ai_lines:
                desc = ai_line.get('description', '')
                acc_id = ai_line.get('account_id')
                if desc and acc_id:
                    ai_map[desc] = acc_id
            # Compare current lines with AI snapshot
            for line in rec.invoice_line_ids:
                if line.display_type not in (False, 'product'):
                    continue
                if not line.name or not line.account_id:
                    continue
                ai_acc_id = ai_map.get(line.name)
                if ai_acc_id and line.account_id.id != ai_acc_id:
                    AiVendorMemory.record_line_correction(
                        self.env,
                        rec.partner_id,
                        line.name,
                        str(ai_acc_id),
                        str(line.account_id.id),
                        company=company,
                    )

    # ===================================================================
    # Invoice line building
    # ===================================================================

    def _ai_apply_lines(self, lines, totals, cache=None, mode='guided', matched_po=None):
        """Create invoice lines from extracted line items."""
        self.ensure_one()
        company = self.company_id or self.env.company
        partner = self.partner_id or None
        if cache is None:
            cache = ai_matcher.VendorMatchCache()
        has_po_line_field = 'purchase_line_id' in self.env['account.move.line']._fields
        move_lines = []
        line_sum = 0.0
        for line_data in lines:
            line_vals = self._ai_build_line_vals(line_data, company, partner, cache=cache, mode=mode)
            if line_vals:
                # PO line matching (guided only)
                if mode == 'guided' and matched_po and has_po_line_field:
                    po_line = ai_matcher.match_purchase_order_line(
                        self.env,
                        matched_po,
                        line_data,
                        partner=partner,
                    )
                    if po_line:
                        line_vals['purchase_line_id'] = po_line.id
                move_lines.append(fields.Command.create(line_vals))
                line_sum += line_vals.get('price_unit', 0.0) * line_vals.get('quantity', 1.0)

        # Warn if extracted line sum diverges from extracted total
        expected = totals.get('untaxed_amount') if totals else None
        if move_lines and expected and abs(line_sum - expected) > 1.0:
            _logger.warning(
                'AI line sum (%.2f) differs from extracted untaxed total (%.2f) for move %s',
                line_sum,
                expected,
                self.id,
            )

        if lines and not move_lines:
            _logger.warning(
                'All %d extracted lines were rejected for move %s',
                len(lines),
                self.id,
            )

        if move_lines:
            self.write({'invoice_line_ids': move_lines})

        # Fix TTC rounding gap (if enabled and within tolerance)
        if move_lines and totals.get('total_amount'):
            self._ai_fix_rounding_gap(totals)

        # Update snapshot with created line data (for account learning)
        self._ai_update_line_snapshot()

    def _ai_update_line_snapshot(self):
        """Update ai_extracted_values with line-level data for correction detection."""
        if not self.ai_extracted_values:
            return
        try:
            snapshot = json.loads(self.ai_extracted_values)
        except (json.JSONDecodeError, TypeError):
            _logger.debug('Invalid ai_extracted_values JSON for move %s, skipping snapshot update.', self.id)
            return
        if '_lines' not in snapshot:
            return
        line_data = []
        for line in self.invoice_line_ids:
            if line.display_type not in (False, 'product'):
                continue
            if line.name and line.account_id:
                line_data.append(
                    {
                        'description': line.name,
                        'account_id': line.account_id.id,
                    }
                )
        snapshot['_lines'] = line_data
        self.ai_extracted_values = json.dumps(snapshot)

    def _ai_build_line_vals(self, line_data, company, partner, cache=None, mode='guided'):
        """Build a single invoice line dict from extracted data."""
        description = line_data.get('description', '')
        if not description:
            return None

        quantity, price_unit = _resolve_line_qty_price(line_data)

        line_vals = {
            'name': description,
            'quantity': quantity,
            'price_unit': price_unit,
        }

        # Tax matching (guided + simplified only)
        if mode != 'free':
            self._ai_match_line_tax(line_data, line_vals, company, partner, cache)

        if mode == 'guided':
            self._ai_match_line_account_and_product(line_data, line_vals, description, company, partner, cache)

        # Discount
        discount = line_data.get('discount_percent')
        if discount:
            line_vals['discount'] = discount

        return line_vals

    def _ai_match_line_tax(self, line_data, line_vals, company, partner, cache):
        """Match tax rate to an Odoo purchase tax."""
        tax_rate = line_data.get('tax_rate')
        if tax_rate is not None:
            tax = ai_matcher.match_tax_by_rate(self.env, tax_rate, company, partner=partner, cache=cache)
            if tax:
                line_vals['tax_ids'] = [fields.Command.set(tax.ids)]

    def _ai_match_line_account_and_product(self, line_data, line_vals, description, company, partner, cache):
        """Match account (memory → standard) and product for a line (guided only)."""
        account_id = self._ai_resolve_account(line_data, description, company, partner, cache)
        if account_id:
            line_vals['account_id'] = account_id
        product_code = line_data.get('product_code')
        if product_code:
            product = ai_matcher.match_product(self.env, product_code, description, partner=partner)
            if product:
                line_vals['product_id'] = product.id

    def _ai_resolve_account(self, line_data, description, company, partner, cache):
        """Resolve account for a line: memory override first, then standard match."""
        # Tier 0: vendor memory account override (learned from corrections)
        override_id = AiVendorMemory.get_account_override(
            self.env,
            partner,
            description,
            company=company,
        )
        if override_id:
            return override_id
        # Use is_shipping_line flag to force shipping category
        category = line_data.get('suggested_account_category', '')
        if line_data.get('is_shipping_line') and not category:
            category = 'shipping'
        account = ai_matcher.match_account(
            self.env,
            category,
            description,
            company,
            partner=partner,
            cache=cache,
        )
        return account.id if account else None
