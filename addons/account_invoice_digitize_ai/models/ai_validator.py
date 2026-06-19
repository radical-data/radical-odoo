import logging
import re

_logger = logging.getLogger(__name__)


def _safe_float(value):
    """Coerce a value to float, returning None if impossible."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _coerce_amounts(data):
    """Coerce string amounts to float in-place (totals + lines)."""
    totals = data.get('totals', {})
    for key in ('untaxed_amount', 'tax_amount', 'total_amount'):
        if key in totals:
            totals[key] = _safe_float(totals[key])
    for line in data.get('lines') or []:
        for key in ('quantity', 'unit_price', 'subtotal_untaxed', 'tax_rate', 'tax_amount'):
            if key in line:
                line[key] = _safe_float(line[key])


def cross_validate(data, detected_number_format=None):
    """Mathematical cross-validation of extracted amounts.

    Checks internal consistency of the extraction result and lowers
    confidence scores in-place when checks fail.  All checks are
    pure arithmetic — no AI needed.

    Confidence penalties are collected first and applied in a single
    pass to prevent cascading degradation between validators.

    Checks performed:
    - ``untaxed_amount + tax_amount ≈ total_amount`` (currency-aware tolerance)
    - Sum of line ``subtotal_untaxed`` ≈ ``untaxed_amount`` (±0.10)
    - Line arithmetic: ``quantity × unit_price ≈ subtotal_untaxed``
    - Tax rate sanity: rates must be between 0% and 100%
    - ``due_date >= invoice_date``
    - IBAN checksum (mod-97 algorithm)
    - Number format consistency (pre-detected vs Claude's response)

    Args:
        data: Parsed extraction dict (modified **in-place**).
        detected_number_format: Format detected by Python regex
            (``'comma_decimal'`` or ``'dot_decimal'``), or ``None``.

    Returns:
        int: Number of validation checks that failed (0 = all passed).
    """
    # Coerce string amounts to float before arithmetic checks
    _coerce_amounts(data)

    # Collect all penalties first (path, max_value) without applying
    penalties = []
    failure_count = 0
    failure_count += _validate_totals(data, penalties)
    failure_count += _validate_line_sums(data, penalties)
    failure_count += _validate_line_arithmetic(data, penalties)
    failure_count += _validate_tax_rates(data, penalties)
    failure_count += _validate_dates(data, penalties)
    failure_count += _validate_date_range(data, penalties)
    failure_count += _validate_iban(data, penalties)
    failure_count += _validate_vat_format(data, penalties)
    failure_count += _validate_tax_line_consistency(data, penalties)
    failure_count += _validate_line_count(data, penalties)
    failure_count += _validate_number_format(data, detected_number_format, penalties)
    failure_count += _validate_qr_data(data, penalties)

    # Apply all penalties in one pass (prevents cascading)
    for target, key, max_val in penalties:
        target[key] = min(target.get(key, 1.0), max_val)

    return failure_count


# Currencies with non-standard decimal places
_ZERO_DECIMAL_CURRENCIES = {
    'BIF',
    'CLP',
    'DJF',
    'GNF',
    'ISK',
    'JPY',
    'KMF',
    'KRW',
    'PYG',
    'RWF',
    'UGX',
    'VND',
    'VUV',
    'XAF',
    'XOF',
    'XPF',
}
_THREE_DECIMAL_CURRENCIES = {'BHD', 'IQD', 'JOD', 'KWD', 'LYD', 'OMR', 'TND'}


def _get_tolerance(data):
    """Return appropriate rounding tolerance based on currency."""
    currency = (data.get('invoice', {}).get('currency') or '').upper()
    if currency in _ZERO_DECIMAL_CURRENCIES:
        return 1.0
    if currency in _THREE_DECIMAL_CURRENCIES:
        return 0.005
    return 0.05


def _validate_totals(data, penalties):
    """Check: untaxed + tax ≈ total (currency-aware tolerance).  Returns 1 on failure, 0 on pass."""
    totals = data.get('totals', {})
    untaxed = totals.get('untaxed_amount') or 0.0
    tax = totals.get('tax_amount') or 0.0
    total = totals.get('total_amount') or 0.0

    tolerance = _get_tolerance(data)
    if total and abs(untaxed + tax - total) > tolerance:
        _logger.warning(
            'Cross-validation: untaxed (%.2f) + tax (%.2f) != total (%.2f)',
            untaxed,
            tax,
            total,
        )
        penalties.append((totals, 'confidence', 0.5))
        return 1
    return 0


def _validate_line_sums(data, penalties):
    """Check: sum of line subtotals ≈ untaxed amount.  Returns 1 on failure, 0 on pass."""
    totals = data.get('totals', {})
    untaxed = totals.get('untaxed_amount') or 0.0
    lines = data.get('lines') or []

    if lines and untaxed:
        line_sum = sum((line.get('subtotal_untaxed') or 0.0) for line in lines)
        if abs(line_sum - untaxed) > _line_sum_tolerance(untaxed):
            _logger.warning(
                'Cross-validation: line sum (%.2f) != untaxed (%.2f)',
                line_sum,
                untaxed,
            )
            for line in lines:
                penalties.append((line, 'confidence', 0.6))
            return 1
    return 0


def _validate_line_arithmetic(data, penalties):
    """Check: quantity × unit_price ≈ subtotal on each line.  Returns failure count."""
    lines = data.get('lines') or []
    failures = 0
    tolerance = _get_tolerance(data)
    for line in lines:
        qty = line.get('quantity')
        price = line.get('unit_price')
        subtotal = line.get('subtotal_untaxed')
        if qty is not None and price is not None and subtotal is not None:
            expected = qty * price
            if abs(expected - subtotal) > max(tolerance, _line_sum_tolerance(subtotal)):
                _logger.warning(
                    'Cross-validation: line qty (%.2f) × price (%.2f) = %.2f != subtotal (%.2f)',
                    qty,
                    price,
                    expected,
                    subtotal,
                )
                penalties.append((line, 'confidence', 0.5))
                failures += 1
    return failures


def _validate_tax_rates(data, penalties):
    """Check: extracted tax rates are within realistic bounds (0–100%).  Returns failure count."""
    lines = data.get('lines') or []
    failures = 0
    for line in lines:
        rate = line.get('tax_rate')
        if rate is not None and (rate < 0 or rate > 100):
            _logger.warning(
                'Cross-validation: unrealistic tax rate %.2f%% on line "%s"',
                rate,
                (line.get('description') or '')[:50],
            )
            penalties.append((line, 'confidence', 0.3))
            failures += 1
    return failures


def _validate_dates(data, penalties):
    """Check: due_date >= invoice_date.  Returns 1 on failure, 0 on pass."""
    inv = data.get('invoice', {})
    inv_date = inv.get('invoice_date')
    due_date = inv.get('due_date')

    if inv_date and due_date and due_date < inv_date:
        _logger.warning(
            'Cross-validation: due_date (%s) < invoice_date (%s)',
            due_date,
            inv_date,
        )
        penalties.append((inv, 'confidence', 0.5))
        return 1
    return 0


def _validate_date_range(data, penalties):
    """Check: invoice_date is not absurdly far in the past or future.

    Invoices dated more than 2 years ago or more than 60 days in the future
    are suspicious (likely an OCR/AI misread).  Returns 1 on failure, 0 on pass.
    """
    from datetime import date, timedelta

    inv = data.get('invoice', {})
    inv_date_str = inv.get('invoice_date')
    if not inv_date_str:
        return 0
    try:
        inv_date = date.fromisoformat(inv_date_str)
    except (ValueError, TypeError):
        return 0
    today = date.today()
    if inv_date < today - timedelta(days=730) or inv_date > today + timedelta(days=60):
        _logger.warning(
            'Cross-validation: invoice_date %s is outside reasonable range',
            inv_date_str,
        )
        penalties.append((inv, 'confidence', 0.5))
        return 1
    return 0


# VAT format patterns (aligned with ai_document.VAT_PATTERNS)
_VAT_FORMAT_RE = re.compile(
    r'^(?:'
    r'FR\d{11}|LU\d{8}|BE0?\d{9,10}|DE\d{9}'
    r'|ES[A-Z0-9]\d{7}[A-Z0-9]|IT\d{11}|NL\d{9}B\d{2}'
    r'|PT\d{9}|ATU\d{8}|CHE?\d{9}|GB\d{9,12}'
    r')$'
)


def _validate_vat_format(data, penalties):
    """Check: vendor VAT looks like a valid European VAT number.

    Only validates the format (not the checksum).  Returns 1 on failure, 0 on pass.
    """
    vendor = data.get('vendor', {})
    vat = vendor.get('vat')
    if not vat:
        return 0
    cleaned = vat.strip().upper().replace(' ', '')
    if not _VAT_FORMAT_RE.match(cleaned):
        _logger.warning('Cross-validation: vendor VAT format invalid: %s', vat)
        penalties.append((vendor, 'confidence', 0.6))
        return 1
    return 0


def _validate_tax_line_consistency(data, penalties):
    """Check: sum of tax_lines base_amount ≈ untaxed, per-line base×rate ≈ tax_amount.

    Returns failure count (0–2).
    """
    tax_lines = data.get('tax_lines', [])
    totals = data.get('totals', {})
    if not tax_lines:
        return 0
    failures = 0
    tolerance = _get_tolerance(data)

    # Sum of base_amounts ≈ untaxed_amount
    untaxed = totals.get('untaxed_amount')
    bases = [tl.get('base_amount') for tl in tax_lines if tl.get('base_amount') is not None]
    if untaxed and bases:
        base_sum = sum(bases)
        if abs(base_sum - untaxed) > max(tolerance, _line_sum_tolerance(untaxed)):
            _logger.warning(
                'Cross-validation: tax line base sum (%.2f) != untaxed (%.2f)',
                base_sum,
                untaxed,
            )
            for tl in tax_lines:
                penalties.append((tl, 'confidence', 0.6))
            failures += 1

    # Per tax line: base × rate ≈ tax_amount
    for tl in tax_lines:
        base = tl.get('base_amount')
        rate = tl.get('tax_rate')
        tax_amt = tl.get('tax_amount')
        if base is not None and rate is not None and tax_amt is not None and rate != 0:
            expected = base * rate / 100.0
            if abs(expected - tax_amt) > max(tolerance, _line_sum_tolerance(tax_amt)):
                _logger.warning(
                    'Cross-validation: tax line base (%.2f) × rate (%.1f%%) = %.2f != tax_amount (%.2f)',
                    base,
                    rate,
                    expected,
                    tax_amt,
                )
                penalties.append((tl, 'confidence', 0.5))
                failures += 1

    return failures


def _validate_line_count(data, penalties):
    """Check: table_analysis.line_count matches actual lines extracted.

    A large mismatch suggests lines were missed or duplicated.
    Returns 1 on failure, 0 on pass.
    """
    table_analysis = data.get('table_analysis', {})
    expected_count = table_analysis.get('line_count')
    lines = data.get('lines') or []
    if not expected_count or not lines:
        return 0
    actual_count = len(lines)
    if abs(expected_count - actual_count) > 1:
        _logger.warning(
            'Cross-validation: table_analysis.line_count (%d) != actual lines (%d)',
            expected_count,
            actual_count,
        )
        penalties.append((table_analysis, 'confidence', 0.6))
        return 1
    return 0


_LINE_SUM_TOLERANCE_BASE = 0.10


_LINE_SUM_TOLERANCE_CAP = 5.0


def _line_sum_tolerance(reference_amount=0.0):
    """Proportional tolerance: 0.10 minimum, 0.1 %% of reference, capped at 5.00."""
    return min(_LINE_SUM_TOLERANCE_CAP, max(_LINE_SUM_TOLERANCE_BASE, abs(reference_amount) * 0.001))


_IBAN_FORMAT_RE = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$')


def _clean_iban(iban):
    """Normalize IBAN: remove spaces/dashes, uppercase."""
    return iban.replace(' ', '').replace('-', '').upper()


def _validate_iban(data, penalties):
    """Validate IBAN checksum using the mod-97 algorithm (ISO 13616).

    Returns 1 on failure, 0 on pass.
    """
    vendor = data.get('vendor', {})
    iban = vendor.get('iban')
    if not iban:
        return 0

    cleaned = _clean_iban(iban)

    if not _IBAN_FORMAT_RE.match(cleaned):
        _logger.warning('Cross-validation: invalid IBAN format: %s', iban)
        penalties.append((vendor, 'confidence', 0.5))
        vendor['iban_valid'] = False
        return 1

    # Mod-97 check: move first 4 chars to end, convert letters to numbers
    rearranged = cleaned[4:] + cleaned[:4]
    numeric = ''.join(str(ord(c) - ord('A') + 10) if c.isalpha() else c for c in rearranged)
    if int(numeric) % 97 != 1:
        _logger.warning('Cross-validation: IBAN checksum failed: %s', iban)
        penalties.append((vendor, 'confidence', 0.5))
        vendor['iban_valid'] = False
        return 1
    vendor['iban_valid'] = True
    return 0


def _validate_number_format(data, detected_format, penalties):
    """Check: pre-detected number format matches Claude's table_analysis.

    If both are present and disagree, the pre-detected format (Python regex
    on raw text) is considered more reliable for text-based PDFs.

    Returns 1 on failure, 0 on pass.
    """
    if not detected_format:
        return 0

    table_analysis = data.get('table_analysis', {})
    claude_format = table_analysis.get('number_format')
    if not claude_format:
        return 0

    if detected_format != claude_format:
        _logger.warning(
            'Cross-validation: number format mismatch — pre-detected %s, Claude returned %s',
            detected_format,
            claude_format,
        )
        penalties.append((table_analysis, 'confidence', 0.6))
        return 1
    return 0


def _validate_qr_data(data, penalties):
    """Cross-validate AI extraction against QR code data.

    QR data (Swiss QR-bill / EPC QR) is treated as high-confidence
    structured source.  When it conflicts with AI extraction, QR wins
    for IBAN, currency, and reference; amount mismatch is penalized
    but not overridden (AI may extract a different total legitimately).

    Returns 0–3 failure count.
    """
    qr_list = data.pop('_qr_data', None)
    if not qr_list:
        return 0

    # Use the first valid QR payload
    qr = qr_list[0]
    failures = 0

    vendor = data.get('vendor', {})
    invoice = data.get('invoice', {})
    totals = data.get('totals', {})

    # --- IBAN ---
    qr_iban = _clean_iban(qr.get('iban') or '')
    ai_iban = _clean_iban(vendor.get('iban') or '')
    if qr_iban:
        if ai_iban and ai_iban != qr_iban:
            _logger.warning(
                'Cross-validation: QR IBAN (%s) != AI IBAN (%s), using QR',
                qr_iban,
                ai_iban,
            )
            penalties.append((vendor, 'confidence', 0.4))
            vendor['iban'] = qr_iban
            failures += 1
        elif not ai_iban:
            vendor['iban'] = qr_iban
            _logger.info('QR code injected IBAN: %s', qr_iban)

    # --- Amount ---
    qr_amount = qr.get('amount')
    ai_total = totals.get('total_amount')
    if qr_amount is not None and ai_total is not None:
        if abs(qr_amount - ai_total) > _get_tolerance(data):
            _logger.warning(
                'Cross-validation: QR amount (%.2f) != AI total (%.2f)',
                qr_amount,
                ai_total,
            )
            penalties.append((totals, 'confidence', 0.4))
            failures += 1

    # --- Currency ---
    qr_currency = (qr.get('currency') or '').upper()
    ai_currency = (invoice.get('currency') or '').upper()
    if qr_currency and ai_currency and qr_currency != ai_currency:
        _logger.warning(
            'Cross-validation: QR currency (%s) != AI currency (%s), using QR',
            qr_currency,
            ai_currency,
        )
        penalties.append((invoice, 'confidence', 0.4))
        invoice['currency'] = qr.get('currency')
        failures += 1

    # --- Reference ---
    qr_ref = qr.get('reference')
    ai_ref = invoice.get('payment_reference')
    if qr_ref and not ai_ref:
        invoice['payment_reference'] = qr_ref
        _logger.info('QR code injected payment reference: %s', qr_ref)

    return failures
