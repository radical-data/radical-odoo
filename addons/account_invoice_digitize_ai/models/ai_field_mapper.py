import json
import logging

from odoo import models

from . import ai_document
from . import ai_facturx_parser
from . import ai_matcher
from .ai_vendor_memory import AiVendorMemory
from .ai_vendor_score import AiVendorScore

_logger = logging.getLogger(__name__)



class AccountMove(models.Model):
    _inherit = 'account.move'

    # ===================================================================
    # Attachment helpers
    # ===================================================================

    def _ai_get_invoice_attachment(self):
        """Return the first PDF or image attachment on this invoice."""
        attachments = self.env['ir.attachment'].search(
            [
                ('res_model', '=', 'account.move'),
                ('res_id', '=', self.id),
            ],
            order='create_date desc',
        )
        for att in attachments:
            mime = (att.mimetype or '').lower()
            if ai_document.is_pdf(mime) or ai_document.is_image(mime):
                return att
        return None

    # ===================================================================
    # Factur-X application
    # ===================================================================

    def _ai_apply_facturx(self, xml_data):
        """Apply Factur-X structured data directly to the invoice (zero AI cost).

        Parses the CII XML into the same dict format as Claude's response,
        then feeds it through the standard extraction pipeline.
        """
        self.ensure_one()
        try:
            data = ai_facturx_parser.parse_facturx_xml(xml_data)
        except Exception as exc:
            _logger.warning('Factur-X XML parsing failed: %s', exc)
            self.ai_extraction_status = 'failed'
            self.ai_confidence = json.dumps({'source': 'facturx', 'overall': 0.0})
            return

        try:
            self._ai_apply_extraction(data)
        except Exception:
            _logger.warning('Factur-X apply failed for move %s', self.id, exc_info=True)
            self.ai_extraction_status = 'failed'

    # ===================================================================
    # Vendor pre-identification
    # ===================================================================

    def _ai_pre_identify_vendor(self, text):
        """Try to identify the vendor from extracted text via VAT number."""
        vat_numbers = ai_document.find_vat_numbers(text)
        if not vat_numbers:
            return None
        # Single query with OR domain instead of N queries
        domain = ['|'] * (len(vat_numbers) - 1) + [('vat', '=ilike', vat) for vat in vat_numbers]
        partner = self.env['res.partner'].search(domain, limit=1)
        return partner or None

    # ===================================================================
    # Buyer verification
    # ===================================================================

    def _ai_verify_buyer(self, buyer_data):
        """Compare extracted buyer info against the active Odoo company.

        Returns a warning dict if the buyer does not match, empty dict otherwise.
        """
        company = self.company_id or self.env.company
        buyer_vat = (buyer_data.get('vat') or '').strip().upper().replace(' ', '')
        buyer_name = (buyer_data.get('name') or '').strip()

        # Check VAT first (most reliable)
        if buyer_vat:
            partner = company.partner_id
            all_vats = {(p.vat or '').strip().upper().replace(' ', '') for p in (partner | partner.child_ids) if p.vat}
            if all_vats:
                if buyer_vat not in all_vats:
                    return {
                        'found': True,
                        'message': self.env._(
                            'Buyer VAT (%s) does not match company %s. This invoice may be addressed to a different entity.'
                        )
                        % (buyer_data.get('vat'), company.name),
                    }
                return {}
            # Company has no VAT → fall through to name check

        # Fallback: name comparison (case-insensitive substring)
        if buyer_name:
            company_name = (company.name or '').strip().upper()
            buyer_upper = buyer_name.upper()
            if company_name and buyer_upper not in company_name and company_name not in buyer_upper:
                return {
                    'found': True,
                    'message': self.env._(
                        'Buyer name "%s" does not match company "%s". '
                        'Please verify this invoice is addressed to the '
                        'correct entity.'
                    )
                    % (buyer_name, company.name),
                }

        return {}

    # ===================================================================
    # Apply extraction to invoice
    # ===================================================================

    def _ai_apply_extraction(self, data, cfg=None):
        """Map extracted data to account.move fields (orchestrator)."""
        self.ensure_one()
        company = self.company_id or self.env.company
        mode = (cfg or self._ai_get_config()).get('extraction_mode', 'guided')
        # Force free mode for customer invoices (no matching, no vendor memory)
        if self._ai_is_customer_invoice():
            mode = 'free'
        cache = ai_matcher.VendorMatchCache()

        # --- Map partner + header fields ---------------------------------
        vals, confidence, partner, matched_po = self._ai_map_header_fields(data, cache=cache, mode=mode)

        # --- Safety checks (warnings) ------------------------------------
        warnings = self._ai_check_warnings(data, partner, vals, company, confidence=confidence, mode=mode, cache=cache)
        confidence.update(warnings)

        # --- Store confidence -------------------------------------------
        totals = data.get('totals', {})
        confidence['totals'] = totals.get('confidence', 0.0)
        self.ai_confidence = json.dumps(confidence)

        # --- Build AI snapshot for correction detection -----------------
        snapshot = {}
        for field in self._AI_TRACKED_FIELDS:
            if field in vals:
                snapshot[field] = str(vals[field]) if vals[field] is not None else ''
        # Include line data for account learning
        lines = data.get('lines')
        if lines:
            snapshot['_lines'] = []
        vals['ai_extracted_values'] = json.dumps(snapshot)

        # Write header values (triggers onchanges in Odoo)
        if vals:
            self.write(vals)

        # --- Invoice lines (if extracted) --------------------------------
        lines = data.get('lines')
        if lines and self.move_type in ('in_invoice', 'in_refund', 'out_invoice', 'out_refund'):
            self._ai_apply_lines(lines, data.get('totals', {}), cache=cache, mode=mode, matched_po=matched_po)

        # --- Update vendor score (guided mode only, vendor bills only) --
        if mode == 'guided' and partner and not self._ai_is_customer_invoice():
            AiVendorScore.update_score(self.env, partner, had_corrections=False, company=company)

    def _ai_map_header_fields(self, data, cache=None, mode='guided'):
        """Match partner, map header fields, detect credit notes.

        Args:
            mode: 'guided' (full matching), 'simplified' (partner only),
                  'free' (no matching).

        Returns (vals, confidence, partner, matched_po).
        """
        company = self.company_id or self.env.company
        vals = {}
        confidence = {}

        partner = self._ai_resolve_partner(data, vals, confidence, company, cache, mode)

        # Invoice header fields (ref, dates, narration, payment reference)
        inv = data.get('invoice', {})
        self._ai_map_invoice_fields(inv, vals, confidence)

        # Payment terms matching (guided only)
        if mode == 'guided':
            self._ai_map_payment_terms(inv, vals, company, partner)

        # Currency
        self._ai_map_currency(inv, vals)

        # Purchase order matching (guided only)
        matched_po = None
        if mode == 'guided':
            po_ref = inv.get('purchase_order_ref')
            if po_ref or partner:
                po_info, matched_po = self._ai_match_purchase_order(po_ref, partner, company, data)
                if po_info:
                    confidence['purchase_order'] = po_info

        # Document type (credit note)
        self._ai_detect_credit_note(data, inv, vals)

        return vals, confidence, partner, matched_po

    def _ai_resolve_partner(self, data, vals, confidence, company, cache, mode):
        """Resolve partner from forced ID, auto-match, or vendor memory override."""
        force_partner_id = data.pop('_force_partner_id', None)
        if force_partner_id:
            partner = self.env['res.partner'].browse(force_partner_id).exists()
            if partner and partner.company_id and partner.company_id != company:
                _logger.warning(
                    'force_partner_id %d restricted to company %s, current is %s — ignoring',
                    force_partner_id, partner.company_id.name, company.name,
                )
                partner = None
            if partner:
                vals['partner_id'] = partner.id
                confidence['partner_id'] = 0.5  # user-selected
                if mode == 'guided':
                    partner = self._ai_apply_partner_overrides(partner, vals, company)
                return partner
        if mode != 'free':
            vendor_data = data.get('vendor', {})
            partner = ai_matcher.match_partner(self.env, vendor_data)
            if partner:
                vals['partner_id'] = partner.id
                confidence['partner_id'] = vendor_data.get('confidence', 0.0)
                if mode == 'guided':
                    partner = self._ai_apply_partner_overrides(partner, vals, company)
                return partner
        return None

    def _ai_map_payment_terms(self, inv, vals, company, partner):
        """Map payment terms text to an Odoo payment term (guided only)."""
        payment_terms_text = inv.get('payment_terms_text')
        if not payment_terms_text:
            return
        payment_term = ai_matcher.match_payment_term(self.env, payment_terms_text, company, partner=partner)
        if payment_term:
            vals['invoice_payment_term_id'] = payment_term.id

    def _ai_detect_credit_note(self, data, inv, vals):
        """Detect and apply credit note type conversion."""
        doc_type = data.get('document_type', 'invoice')
        if doc_type == 'credit_note' or inv.get('is_credit_note'):
            if self.move_type == 'in_invoice':
                vals['move_type'] = 'in_refund'
            elif self.move_type == 'out_invoice':
                vals['move_type'] = 'out_refund'

    @staticmethod
    def _ai_map_invoice_fields(inv, vals, confidence):
        """Map simple invoice header fields (ref, dates, narration, payment ref)."""
        field_map = {
            'reference': ('ref', True),
            'invoice_date': ('invoice_date', True),
            'due_date': ('invoice_date_due', True),
            'narration': ('narration', False),
            'payment_reference': ('payment_reference', False),
        }
        inv_confidence = inv.get('confidence', 0.0)
        for src_key, (dst_key, track_confidence) in field_map.items():
            value = inv.get(src_key)
            if value:
                vals[dst_key] = value
                if track_confidence:
                    confidence[dst_key] = inv_confidence

    def _ai_map_currency(self, inv, vals):
        """Map extracted currency code to an Odoo ``res.currency``."""
        currency_code = inv.get('currency')
        if not currency_code:
            return
        currency = self.env['res.currency'].search(
            [('name', '=ilike', currency_code), ('active', '=', True)],
            limit=1,
        )
        if currency and currency != self.currency_id:
            vals['currency_id'] = currency.id

    def _ai_apply_partner_overrides(self, partner, vals, company):
        """Apply vendor memory overrides to partner selection.

        Returns the (possibly overridden) partner.
        """
        overrides = AiVendorMemory.get_auto_apply_overrides(self.env, partner, company=company)
        if overrides.get('partner_id'):
            try:
                override_partner = self.env['res.partner'].browse(int(overrides['partner_id']))
                if override_partner.exists():
                    vals['partner_id'] = override_partner.id
                    return override_partner
            except (ValueError, TypeError):
                _logger.debug('Failed to apply partner_id override from vendor memory.')
        return partner

    def _ai_match_purchase_order(self, po_ref, partner, company, data):
        """Attempt PO matching. Returns ``(confidence_dict, po_record)``."""
        totals = data.get('totals', {})
        inv = data.get('invoice', {})
        po, tier = ai_matcher.match_purchase_order(
            self.env,
            po_ref,
            partner=partner,
            company=company,
            total_amount=totals.get('total_amount'),
            invoice_date=inv.get('invoice_date'),
        )
        if not po:
            if po_ref:
                return {
                    'ref': po_ref,
                    'matched': False,
                    'message': self.env._('PO reference "%s" found but no matching purchase order in Odoo.') % po_ref,
                }, None
            return None, None

        score = {'exact': 0.95, 'fuzzy': 0.7, 'amount_date': 0.5}.get(tier, 0.5)
        return {
            'ref': po_ref or po.name,
            'matched': True,
            'po_name': po.name,
            'po_amount': po.amount_total,
            'match_tier': tier,
            'confidence': score,
        }, po

    # ===================================================================
    # Safety checks (warnings)
    # ===================================================================

    def _ai_check_warnings(self, data, partner, vals, company, confidence=None, mode='guided', cache=None):
        """Run safety checks: proforma, paid stamp, buyer, duplicate, anomaly, PO.

        Args:
            mode: 'guided' (all checks), 'simplified' (proforma + paid),
                  'free' (proforma + paid only).
            cache: Optional VendorMatchCache to avoid duplicate DB queries.

        Returns a dict of warning entries to merge into confidence.
        """
        warnings = {}

        # Pro-forma warning (always)
        doc_type = data.get('document_type', 'invoice')
        if doc_type == 'proforma':
            warnings['proforma_warning'] = {
                'found': True,
                'message': self.env._(
                    'This document appears to be a pro-forma/quote and should NOT be recorded as an accounting entry.'
                ),
            }

        # Paid stamp warning (always)
        if data.get('is_marked_paid'):
            warnings['paid_warning'] = {
                'found': True,
                'message': self.env._(
                    'This document appears to be marked as already paid. '
                    'Creating a payment may result in double payment.'
                ),
            }

        # Reverse charge warning (always)
        inv = data.get('invoice', {})
        if inv.get('is_reverse_charge'):
            rc_text = inv.get('reverse_charge_text') or ''
            msg = self.env._(
                'This invoice mentions reverse charge / autoliquidation. '
                'Tax should be self-assessed by the buyer — verify that '
                'no input VAT is recorded.'
            )
            if rc_text:
                msg += ' (%s)' % rc_text
            warnings['reverse_charge_warning'] = {
                'found': True,
                'message': msg,
            }

        if mode == 'free':
            return warnings

        # Buyer verification (guided + simplified)
        buyer_data = data.get('buyer', {})
        if buyer_data.get('name') or buyer_data.get('vat'):
            buyer_warning = self._ai_verify_buyer(buyer_data)
            if buyer_warning.get('found'):
                warnings['buyer_warning'] = buyer_warning

        # Tax rate mismatch warning (guided + simplified)
        self._ai_check_tax_warnings(data, company, warnings, cache=cache)

        if mode == 'guided':
            self._ai_check_guided_warnings(data, partner, vals, company, confidence, warnings)

        return warnings

    def _ai_check_tax_warnings(self, data, company, warnings, cache=None):
        """Warn when extracted tax rates have no matching purchase tax in Odoo."""
        tax_lines = data.get('tax_lines', [])
        if not tax_lines:
            return
        extracted_rates = {tl.get('tax_rate') for tl in tax_lines if tl.get('tax_rate') is not None}
        if not extracted_rates:
            return
        if cache:
            purchase_taxes = cache.get_all_purchase_taxes(self.env, company)
        else:
            purchase_taxes = self.env['account.tax'].search([
                ('type_tax_use', '=', 'purchase'),
                ('active', '=', True),
                ('company_id', '=', company.id),
            ])
        available_rates = {t.amount for t in purchase_taxes}
        unmatched = sorted(r for r in extracted_rates if r not in available_rates)
        if unmatched:
            rates_str = ', '.join('%.1f%%' % r for r in unmatched)
            warnings['tax_warning'] = {
                'found': True,
                'message': self.env._(
                    'Tax rates %s were extracted but no matching purchase tax '
                    'exists in Odoo. Please create the missing taxes in '
                    'Accounting > Configuration > Taxes.'
                ) % rates_str,
            }

    def _ai_check_guided_warnings(self, data, partner, vals, company, confidence, warnings):
        """Guided-mode safety checks: duplicate, anomaly, PO warnings."""
        from . import ai_anomaly_detector
        from . import ai_duplicate_detector

        totals = data.get('totals', {})
        dup = ai_duplicate_detector.detect_duplicates(
            self.env,
            self,
            partner,
            vals.get('ref'),
            vals.get('invoice_date'),
            totals.get('total_amount'),
            company=company,
        )
        if dup.get('found'):
            warnings['duplicate_warning'] = dup

        anomaly = ai_anomaly_detector.detect_anomalies(
            self.env,
            partner,
            totals.get('total_amount'),
            company=company,
        )
        if anomaly.get('found'):
            warnings['anomaly_warning'] = anomaly

        po_info = (confidence or {}).get('purchase_order', {})
        if po_info and not po_info.get('matched') and po_info.get('ref'):
            warnings['po_warning'] = {
                'found': True,
                'message': po_info.get('message', ''),
            }
        elif po_info and po_info.get('match_tier') == 'amount_date':
            warnings['po_warning'] = {
                'found': True,
                'message': self.env._('PO matched by amount/date only (no ref on invoice). Matched: %s. Please verify.')
                % po_info.get('po_name', ''),
            }
