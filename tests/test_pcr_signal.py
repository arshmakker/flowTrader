"""
Unit tests for pcr_signal.get_weekly_pcr and _resolve_weekly_tsym.
All broker API calls are mocked — no live connection required.
"""

import time
from unittest.mock import MagicMock

import pytest

import trading_system.core.pcr_signal as pcr_mod
from trading_system.core.pcr_signal import _resolve_weekly_tsym, get_weekly_pcr

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_api(
    searchscrip_tsym="NIFTY26MAY26C24000",
    chain_rows=None,
    quotes_oi=None,
):
    """Return a mock api with sensible defaults for PCR tests."""
    api = MagicMock()

    # searchscrip — returns a single matching OPTIDX result
    api.searchscrip.return_value = {
        "stat": "Ok",
        "values": [{"tsym": searchscrip_tsym, "instname": "OPTIDX"}],
    }

    # get_option_chain — returns CE + PE rows for the weekly expiry
    if chain_rows is None:
        chain_rows = [
            {"tsym": "NIFTY26MAY26C24000", "token": "1001", "optt": "CE", "strprc": "24000"},
            {"tsym": "NIFTY26MAY26C24050", "token": "1003", "optt": "CE", "strprc": "24050"},
            {"tsym": "NIFTY26MAY26P24000", "token": "1002", "optt": "PE", "strprc": "24000"},
            {"tsym": "NIFTY26MAY26P24050", "token": "1004", "optt": "PE", "strprc": "24050"},
        ]
    api.get_option_chain.return_value = {"stat": "Ok", "values": chain_rows}

    # get_quotes — CE tokens get 1_000_000 OI, PE tokens get 1_300_000
    if quotes_oi is None:
        quotes_oi = {"1001": 1_000_000, "1002": 1_300_000, "1003": 900_000, "1004": 1_100_000}

    def _quotes(exchange, token):
        oi = quotes_oi.get(token, 500_000)
        return {"stat": "Ok", "oi": str(oi)}

    api.get_quotes.side_effect = _quotes
    return api


def _clear_caches():
    """Reset module-level caches between tests."""
    pcr_mod._pcr_cache.clear()
    pcr_mod._tsym_cache.clear()


# ── _resolve_weekly_tsym ───────────────────────────────────────────────────────


class TestResolveWeeklyTsym:
    def setup_method(self):
        _clear_caches()

    def test_returns_valid_tsym(self):
        api = _make_api(searchscrip_tsym="NIFTY26MAY26C24000")
        result = _resolve_weekly_tsym(api, "NIFTY", 24000)
        assert result == "NIFTY26MAY26C24000"

    def test_cached_on_second_call(self):
        api = _make_api(searchscrip_tsym="NIFTY26MAY26C24000")
        _resolve_weekly_tsym(api, "NIFTY", 24000)
        count_after_first = api.searchscrip.call_count
        assert count_after_first >= 1  # at least one call to find the match
        _resolve_weekly_tsym(api, "NIFTY", 24000)
        # Second call must not hit searchscrip again — served from cache
        assert api.searchscrip.call_count == count_after_first

    def test_returns_none_when_not_found(self):
        api = MagicMock()
        api.searchscrip.return_value = {"stat": "Ok", "values": []}
        result = _resolve_weekly_tsym(api, "NIFTY", 24000)
        assert result is None

    def test_skips_non_optidx(self):
        api = MagicMock()
        api.searchscrip.return_value = {
            "stat": "Ok",
            "values": [{"tsym": "NIFTY26MAY26C24000", "instname": "EQ"}],
        }
        result = _resolve_weekly_tsym(api, "NIFTY", 24000)
        assert result is None

    def test_handles_searchscrip_exception(self):
        api = MagicMock()
        api.searchscrip.side_effect = Exception("network error")
        result = _resolve_weekly_tsym(api, "NIFTY", 24000)
        assert result is None


# ── get_weekly_pcr ─────────────────────────────────────────────────────────────


class TestGetWeeklyPcr:
    def setup_method(self):
        _clear_caches()

    def test_computes_correct_pcr(self):
        api = _make_api()
        # CE OI = 1_000_000 + 900_000 = 1_900_000
        # PE OI = 1_300_000 + 1_100_000 = 2_400_000
        # PCR = 2_400_000 / 1_900_000 ≈ 1.263
        pcr = get_weekly_pcr(api, spot=24000.0)
        assert pcr == pytest.approx(2_400_000 / 1_900_000, rel=1e-3)

    def test_returns_none_when_tsym_unresolvable(self):
        api = MagicMock()
        api.searchscrip.return_value = {"stat": "Ok", "values": []}
        pcr = get_weekly_pcr(api, spot=24000.0)
        assert pcr is None

    def test_returns_none_when_chain_fails(self):
        api = _make_api()
        api.get_option_chain.return_value = {"stat": "Not_Ok", "emsg": "error"}
        pcr = get_weekly_pcr(api, spot=24000.0)
        assert pcr is None

    def test_returns_none_when_ce_oi_zero(self):
        api = _make_api(quotes_oi={"1001": 0, "1002": 1_000_000, "1003": 0, "1004": 500_000})
        pcr = get_weekly_pcr(api, spot=24000.0)
        assert pcr is None

    def test_result_is_cached(self):
        api = _make_api()
        pcr1 = get_weekly_pcr(api, spot=24000.0)
        pcr2 = get_weekly_pcr(api, spot=24000.0)
        assert pcr1 == pcr2
        # get_option_chain only called once — second call uses cache
        assert api.get_option_chain.call_count == 1

    def test_cache_expires(self):
        api = _make_api()
        get_weekly_pcr(api, spot=24000.0)
        # Backdate the cache entry to simulate expiry
        pcr_mod._pcr_cache["NIFTY"] = (0.5, time.time() - 400)
        get_weekly_pcr(api, spot=24000.0)
        assert api.get_option_chain.call_count == 2

    def test_instrument_param(self):
        api = _make_api(searchscrip_tsym="BANKNIFTY26MAY26C52000")
        api.get_option_chain.return_value = {
            "stat": "Ok",
            "values": [
                {"tsym": "BANKNIFTY26MAY26C52000", "token": "2001", "optt": "CE", "strprc": "52000"},
                {"tsym": "BANKNIFTY26MAY26P52000", "token": "2002", "optt": "PE", "strprc": "52000"},
            ],
        }
        api.get_quotes.side_effect = lambda exchange, token: {
            "stat": "Ok",
            "oi": "2000000" if token == "2001" else "3000000",
        }
        pcr = get_weekly_pcr(api, spot=52000.0, instrument="BANKNIFTY")
        assert pcr == pytest.approx(3_000_000 / 2_000_000, rel=1e-3)

    def test_atm_rounded_to_nearest_50(self):
        """spot=24010 → ATM=24000 (round to nearest 50)."""
        api = _make_api()
        get_weekly_pcr(api, spot=24010.0)
        call_kwargs = api.get_option_chain.call_args.kwargs
        assert call_kwargs["strikeprice"] == "24000"
