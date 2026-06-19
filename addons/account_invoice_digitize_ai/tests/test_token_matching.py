"""Tests for token-based fuzzy partner matching."""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import (
    _normalize_company_name,
    _token_match_score,
    match_partner,
)


@tagged('post_install', '-at_install')
class TestTokenMatching(TransactionCase):
    """Test token-based fuzzy company name matching (tier 2b)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env['res.partner']

    def _create_company(self, name, **kwargs):
        vals = {'name': name, 'is_company': True, 'active': True}
        vals.update(kwargs)
        return self.Partner.create(vals)

    # ---------------------------------------------------------------
    # _normalize_company_name
    # ---------------------------------------------------------------

    def test_normalize_strips_legal_suffix(self):
        """Legal suffixes (SA, GmbH, etc.) should be removed."""
        tokens = _normalize_company_name('Infomaniak Network SA')
        self.assertIn('infomaniak', tokens)
        self.assertIn('network', tokens)
        self.assertNotIn('sa', tokens)

    def test_normalize_strips_multiple_suffixes(self):
        """Multiple legal suffixes should all be removed."""
        tokens = _normalize_company_name('ACME Corp Ltd')
        self.assertIn('acme', tokens)
        self.assertNotIn('corp', tokens)
        self.assertNotIn('ltd', tokens)

    def test_normalize_empty_name(self):
        """Empty or None name should return empty frozenset."""
        self.assertEqual(_normalize_company_name(''), frozenset())
        self.assertEqual(_normalize_company_name(None), frozenset())

    def test_normalize_short_tokens_filtered(self):
        """Tokens shorter than 2 chars should be filtered out."""
        tokens = _normalize_company_name('A B Company')
        self.assertNotIn('a', tokens)
        self.assertNotIn('b', tokens)
        self.assertIn('company', tokens)

    # ---------------------------------------------------------------
    # _token_match_score
    # ---------------------------------------------------------------

    def test_score_identical_sets(self):
        """Identical token sets should score 1.0."""
        tokens = frozenset(['infomaniak', 'network'])
        self.assertAlmostEqual(_token_match_score(tokens, tokens), 1.0)

    def test_score_subset(self):
        """A subset should score 1.0 (asymmetric Jaccard)."""
        a = frozenset(['infomaniak'])
        b = frozenset(['infomaniak', 'network'])
        self.assertAlmostEqual(_token_match_score(a, b), 1.0)

    def test_score_no_overlap(self):
        """Disjoint sets should score 0.0."""
        a = frozenset(['alpha', 'beta'])
        b = frozenset(['gamma', 'delta'])
        self.assertAlmostEqual(_token_match_score(a, b), 0.0)

    def test_score_empty_set(self):
        """Empty set should score 0.0."""
        self.assertAlmostEqual(_token_match_score(frozenset(), frozenset(['a'])), 0.0)
        self.assertAlmostEqual(_token_match_score(frozenset(['a']), frozenset()), 0.0)

    # ---------------------------------------------------------------
    # match_partner with token matching
    # ---------------------------------------------------------------

    def test_abbreviated_name_matches(self):
        """'Infomaniak' should match 'Infomaniak Network SA'."""
        self._create_company('Infomaniak Network SA')
        result = match_partner(self.env, {'name': 'Infomaniak'})
        self.assertTrue(result)
        self.assertIn('Infomaniak', result.name)

    def test_reordered_name_matches(self):
        """'Services ACME' should match 'ACME Services SARL'."""
        self._create_company('ACME Services SARL')
        result = match_partner(self.env, {'name': 'Services ACME'})
        self.assertTrue(result)
        self.assertIn('ACME', result.name)

    def test_legal_suffix_ignored_in_matching(self):
        """Different legal suffixes should not prevent matching."""
        self._create_company('Société Générale SA')
        result = match_partner(self.env, {'name': 'Société Générale GmbH'})
        self.assertTrue(result)
        self.assertIn('Générale', result.name)

    def test_vat_still_preferred_over_tokens(self):
        """VAT match should take priority over token match."""
        p_vat = self._create_company('VAT Company', vat='CH123456789')
        self._create_company('Token Company')
        result = match_partner(self.env, {'name': 'Token Company', 'vat': 'CH123456789'})
        self.assertEqual(result.id, p_vat.id)

    def test_no_match_below_threshold(self):
        """Unrelated names should not match."""
        self._create_company('Alpha Beta Gamma')
        result = match_partner(self.env, {'name': 'Omega Zeta Epsilon'})
        self.assertFalse(result)

    def test_empty_name_no_crash(self):
        """Empty name should return None without error."""
        result = match_partner(self.env, {'name': ''})
        self.assertFalse(result)

    def test_none_name_no_crash(self):
        """None name should return None without error."""
        result = match_partner(self.env, {'name': None})
        self.assertFalse(result)

    def test_exact_match_still_preferred(self):
        """Exact ilike match (tier 2a) should be preferred over token match."""
        p_exact = self._create_company('Exact Match Corp')
        self._create_company('Exact Match Services SARL')
        result = match_partner(self.env, {'name': 'Exact Match Corp'})
        self.assertEqual(result.id, p_exact.id)
