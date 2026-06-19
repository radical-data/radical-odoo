"""Document processing utilities for AI extraction.

Pure utility functions for PDF text extraction, Factur-X detection,
vendor pre-identification, table extraction, and attachment type detection.
These are not Odoo models — they are imported by account_move.py.
"""

import io
import logging
import re

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PDF text extraction — try PyPDF2 first (Odoo 16/17), then pypdf (Odoo 18)
# ---------------------------------------------------------------------------
try:
    from PyPDF2 import PdfReader as _PdfReader  # noqa: N811
except ImportError:
    try:
        from pypdf import PdfReader as _PdfReader  # noqa: N811
    except ImportError:
        _PdfReader = None

# ---------------------------------------------------------------------------
# Factur-X / ZUGFeRD — optional dependency
# ---------------------------------------------------------------------------
try:
    from facturx import get_xml_from_pdf as _get_facturx_xml

    FACTURX_AVAILABLE = True
except ImportError:
    _get_facturx_xml = None
    FACTURX_AVAILABLE = False

# ---------------------------------------------------------------------------
# pdfplumber — optional dependency for structured table extraction (Step 2g)
# ---------------------------------------------------------------------------
try:
    import pdfplumber as _pdfplumber

    PDFPLUMBER_AVAILABLE = True
except ImportError:
    _pdfplumber = None
    PDFPLUMBER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Supported image MIME types for vision mode
# ---------------------------------------------------------------------------
IMAGE_MIMES = {
    'image/png': 'image/png',
    'image/jpeg': 'image/jpeg',
    'image/gif': 'image/gif',
    'image/webp': 'image/webp',
}

# ---------------------------------------------------------------------------
# VAT number regex patterns (European countries)
# ---------------------------------------------------------------------------
VAT_PATTERNS = [
    r'\b(FR\d{11})\b',
    r'\b(LU\d{8})\b',
    r'\b(BE0?\d{9,10})\b',
    r'\b(DE\d{9})\b',
    r'\b(ES[A-Z0-9]\d{7}[A-Z0-9])\b',
    r'\b(IT\d{11})\b',
    r'\b(NL\d{9}B\d{2})\b',
    r'\b(PT\d{9})\b',
    r'\b(AT\s?U\d{8})\b',
    r'\b(CH\s?E?\d{9})\b',
    r'\b(GB\d{9,12})\b',
]


def is_pdf(mimetype):
    """Return True if the MIME type indicates a PDF."""
    return 'pdf' in (mimetype or '').lower()


def is_image(mimetype):
    """Return True if the MIME type is a supported image format."""
    return mimetype in IMAGE_MIMES


def extract_text_from_pdf(pdf_bytes):
    """Extract text from a PDF using PyPDF2/pypdf.

    Returns concatenated text from all pages, separated by page breaks.
    Returns empty string if extraction fails or no reader is available.
    """
    if _PdfReader is None:
        _logger.warning('No PDF reader available (PyPDF2/pypdf not installed)')
        return ''
    try:
        reader = _PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages_text.append(page_text)
        return '\n\n--- Page break ---\n\n'.join(pages_text)
    except Exception:
        _logger.exception('Failed to extract text from PDF')
        return ''


def detect_facturx(pdf_bytes):
    """Check for embedded Factur-X/ZUGFeRD XML in a PDF.

    Returns XML string if found, None otherwise.
    """
    if not FACTURX_AVAILABLE or _get_facturx_xml is None:
        return None
    try:
        _, xml_str = _get_facturx_xml(io.BytesIO(pdf_bytes))
        if xml_str:
            return xml_str
    except Exception:
        _logger.debug('No Factur-X data found (or parsing failed)', exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Number format detection — decimal separator heuristic (Step 2e)
# ---------------------------------------------------------------------------

# Patterns that end with a comma + exactly 2 digits → comma_decimal (FR/DE/ES)
# e.g. 1.234,56  or  1 234,56  or  234,56
_COMMA_DECIMAL_RE = re.compile(
    r'(?<!\d)'  # not preceded by another digit
    r'[\d][\d. \x27]*'  # integer part (digits, dots, spaces, apostrophe as thousands sep)
    r',\d{2}'  # comma + exactly 2 decimal digits
    r'(?!\d)',  # not followed by another digit
)

# Patterns that end with a dot + exactly 2 digits → dot_decimal (EN/US)
# e.g. 1,234.56  or  1'234.56  or  234.56
_DOT_DECIMAL_RE = re.compile(
    r'(?<!\d)'
    r'[\d][\d,\x27 ]*'  # integer part (digits, commas, apostrophe, spaces)
    r'\.\d{2}'  # dot + exactly 2 decimal digits
    r'(?!\d)',
)


def detect_number_format(text):
    """Detect the decimal separator convention used in the document text.

    Scans for monetary-style numbers (amounts with exactly 2 decimal places)
    and determines whether the document uses:
    - ``comma_decimal`` (1.234,56 / 1 234,56) — FR, DE, ES, IT, NL, PT, BR
    - ``dot_decimal``   (1,234.56 / 1'234.56) — EN, US, UK, CH, Asia

    Returns ``None`` if fewer than 2 amounts are found (insufficient data).
    """
    comma_hits = len(_COMMA_DECIMAL_RE.findall(text))
    dot_hits = len(_DOT_DECIMAL_RE.findall(text))

    total = comma_hits + dot_hits
    if total < 2:
        return None

    if comma_hits > dot_hits:
        return 'comma_decimal'
    if dot_hits > comma_hits:
        return 'dot_decimal'
    # Tie — ambiguous
    return None


# ---------------------------------------------------------------------------
# Document language detection — keyword-based heuristic
# ---------------------------------------------------------------------------

_LANG_KEYWORDS = {
    'fr': {'facture', 'avoir', 'montant', 'échéance', 'tva', 'total', 'remise', 'unitaire', 'règlement', 'devise'},
    'de': {'rechnung', 'gutschrift', 'betrag', 'mwst', 'gesamt', 'rabatt', 'stück', 'zahlung', 'währung', 'netto'},
    'en': {'invoice', 'amount', 'total', 'subtotal', 'discount', 'quantity', 'due', 'payment', 'balance', 'net'},
    'es': {'factura', 'importe', 'descuento', 'cantidad', 'vencimiento', 'iva', 'pago', 'neto', 'moneda', 'plazo'},
    'it': {'fattura', 'importo', 'sconto', 'quantità', 'scadenza', 'iva', 'pagamento', 'netto', 'valuta', 'totale'},
    'nl': {'factuur', 'bedrag', 'korting', 'aantal', 'btw', 'totaal', 'betaling', 'netto', 'valuta', 'vervaldatum'},
    'pt': {'fatura', 'valor', 'desconto', 'quantidade', 'iva', 'total', 'pagamento', 'líquido', 'moeda', 'prazo'},
}

_LANG_NAMES = {
    'fr': 'French',
    'de': 'German',
    'en': 'English',
    'es': 'Spanish',
    'it': 'Italian',
    'nl': 'Dutch',
    'pt': 'Portuguese',
}

# Minimum keyword hits to consider a language detected
_LANG_MIN_HITS = 3


def detect_language(text):
    """Detect document language from keyword frequency.

    Scans for common invoice-related keywords in supported languages.
    Returns ``(lang_code, lang_name)`` (e.g. ``('fr', 'French')``) or
    ``(None, None)`` if detection is ambiguous or insufficient.

    Requires at least 3 keyword hits and the top language must have at
    least twice as many hits as the runner-up.
    """
    if not text:
        return None, None

    words = set(re.findall(r'\w+', text.lower()))

    scores = {}
    for lang, keywords in _LANG_KEYWORDS.items():
        hits = len(words & keywords)
        if hits > 0:
            scores[lang] = hits

    if not scores:
        return None, None

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_lang, best_hits = ranked[0]

    if best_hits < _LANG_MIN_HITS:
        return None, None

    # Require clear winner: at least 2x the runner-up
    if len(ranked) >= 2:
        _, second_hits = ranked[1]
        if best_hits < second_hits * 2:
            return None, None

    return best_lang, _LANG_NAMES.get(best_lang, best_lang)


# ---------------------------------------------------------------------------
# PDF metadata extraction (Step 2a)
# ---------------------------------------------------------------------------


_PDF_METADATA_FIELDS = {
    'author': '/Author',
    'creator': '/Creator',
    'producer': '/Producer',
    'title': '/Title',
    'subject': '/Subject',
    'creation_date': '/CreationDate',
}


def extract_pdf_metadata(pdf_bytes):
    """Extract metadata from a PDF (author, creator, title, creation date).

    Uses PyPDF2/pypdf which is already available for text extraction.
    Returns a dict with non-empty string values only.  Returns ``{}`` on
    failure or if no reader is available (graceful degradation).
    """
    if _PdfReader is None:
        return {}
    try:
        reader = _PdfReader(io.BytesIO(pdf_bytes))
        raw = reader.metadata
        if not raw:
            return {}

        result = {}
        for key, pdf_key in _PDF_METADATA_FIELDS.items():
            val = raw.get(pdf_key)
            if val:
                val = str(val).strip()
                if val:
                    result[key] = val
        return result
    except Exception:
        _logger.debug('Failed to extract PDF metadata', exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# VAT number detection
# ---------------------------------------------------------------------------


def find_vat_numbers(text):
    """Search text for European VAT number patterns.

    Returns list of cleaned VAT numbers found (uppercase, no spaces).
    """
    results = []
    for pattern in VAT_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            vat = match.group(1).replace(' ', '').upper()
            if vat not in results:
                results.append(vat)
    return results


# ---------------------------------------------------------------------------
# Structured table extraction — pdfplumber (Step 2g)
# ---------------------------------------------------------------------------

# Maximum pages to process for table extraction (performance guard)
MAX_TABLE_PAGES = 50

# Regex to detect at least one digit in a cell (for numeric column check)
_HAS_DIGIT_RE = re.compile(r'\d')


def extract_tables_from_pdf(pdf_bytes):
    """Extract structured tables from a text-based PDF using pdfplumber.

    Returns a list of table dicts, each with:
      - ``headers``: list of column header strings
      - ``rows``: list of lists (each inner list = one data row)
      - ``page_numbers``: list of 1-based page numbers the table spans

    Returns ``[]`` if pdfplumber is not installed, no valid tables are found,
    or extraction fails.  Graceful degradation — never raises.
    """
    if not PDFPLUMBER_AVAILABLE:
        return []
    try:
        raw_tables = []
        with _pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) > MAX_TABLE_PAGES:
                _logger.info(
                    'PDF has %d pages, limiting table extraction to first %d',
                    len(pdf.pages),
                    MAX_TABLE_PAGES,
                )
            for page_idx, page in enumerate(pdf.pages[:MAX_TABLE_PAGES]):
                for table in page.extract_tables():
                    if not table or len(table) < 2:
                        continue
                    # Clean cells: None → '', strip whitespace
                    cleaned = []
                    for row in table:
                        cleaned.append([str(cell).strip() if cell is not None else '' for cell in row])
                    header = cleaned[0]
                    data_rows = cleaned[1:]
                    raw_tables.append((page_idx + 1, header, data_rows))

        merged = _merge_multipage_tables(raw_tables)
        return [t for t in merged if _validate_table(t)]
    except Exception:
        _logger.debug('Failed to extract tables from PDF with pdfplumber', exc_info=True)
        return []


def _merge_multipage_tables(raw_tables):
    """Merge tables across pages that share the same column structure.

    Args:
        raw_tables: list of ``(page_number, header_row, data_rows)`` tuples.

    Returns:
        list of dicts ``{'headers': [...], 'rows': [...], 'page_numbers': [...]}``.
    """
    if not raw_tables:
        return []

    result = []
    cur_page, cur_headers, cur_rows = raw_tables[0]
    cur_pages = [cur_page]

    for page_num, header, rows in raw_tables[1:]:
        # Check if this table continues the current one:
        # same column count AND first row resembles current headers
        if len(header) == len(cur_headers) and _headers_match(cur_headers, header):
            # Continuation — skip repeated header, append rows
            cur_rows.extend(rows)
            if page_num not in cur_pages:
                cur_pages.append(page_num)
        else:
            # Different table — finalize current and start new
            result.append({'headers': cur_headers, 'rows': cur_rows, 'page_numbers': cur_pages})
            cur_headers = header
            cur_rows = list(rows)
            cur_pages = [page_num]

    result.append({'headers': cur_headers, 'rows': cur_rows, 'page_numbers': cur_pages})
    return result


def _headers_match(headers_a, headers_b):
    """Return True if two header rows are similar enough to be the same table.

    Compares case-insensitively; at least 50% of columns must match.
    """
    if len(headers_a) != len(headers_b):
        return False
    matches = sum(1 for a, b in zip(headers_a, headers_b) if a.lower().strip() == b.lower().strip())
    return matches >= len(headers_a) * 0.5


def _validate_table(table):
    """Check if an extracted table has valid structure for invoice data.

    A table is valid if:
    - At least 75% of rows have the same column count as the headers
    - At least one column contains numeric content (has a digit)
    - At least 1 non-empty data row
    """
    headers = table.get('headers', [])
    rows = table.get('rows', [])
    if not rows:
        return False

    expected_cols = len(headers)

    # Column count consistency (≥75%)
    if expected_cols > 0:
        consistent = sum(1 for row in rows if len(row) == expected_cols)
        if consistent / len(rows) < 0.75:
            return False

    # At least one numeric cell anywhere in the data rows
    has_numeric = any(_HAS_DIGIT_RE.search(cell) for row in rows for cell in row)
    if not has_numeric:
        return False

    # At least one non-empty data row
    has_data = any(any(cell.strip() for cell in row) for row in rows)
    return has_data


def format_tables_as_markdown(tables):
    """Format extracted tables as markdown for inclusion in the AI prompt.

    Args:
        tables: list of table dicts from :func:`extract_tables_from_pdf`.

    Returns:
        Markdown-formatted string, or ``''`` if no tables.
    """
    if not tables:
        return ''

    parts = []
    for idx, table in enumerate(tables):
        headers = table['headers']
        rows = table['rows']
        page_nums = table.get('page_numbers', [])

        if len(tables) > 1:
            page_label = ', '.join(str(p) for p in page_nums) if page_nums else '?'
            parts.append(f'Table {idx + 1} (page{"s" if len(page_nums) > 1 else ""} {page_label}):')

        # Header row
        parts.append('| ' + ' | '.join(headers) + ' |')
        # Separator row
        parts.append('|' + '|'.join('---' for _ in headers) + '|')
        # Data rows
        for row in rows:
            # Pad or truncate row to match header count
            padded = list(row) + [''] * max(0, len(headers) - len(row))
            parts.append('| ' + ' | '.join(padded[: len(headers)]) + ' |')

        parts.append('')  # blank line after table

    return '\n'.join(parts).rstrip()
