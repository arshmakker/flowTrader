"""Standalone MCX tick collector for the liquid-5 commodity futures.

Scope: GOLD, SILVER, CRUDEOIL, COPPER, NATURALGAS — front-2 expiries each, 10
contracts total. Polls Shoonya `get_quotes` at 5s cadence during the MCX
session (09:00–23:30 IST, Mon–Fri) and appends ticks to per-day CSV under
`market_data_YYYYMMDD/raw_data/futures/` (mirrors the existing schema so the
files coexist with the NSE/NFO data the trading system already collects).

Decoupled from `main.py` and `data_collector.py` by design: the paper trader's
9:15–15:30 lifecycle and the MCX 09:00–23:30 lifecycle are different concerns.
Auth reuses the cached Access_token in `cred.yml`; on auth failure the
collector exits cleanly so it never re-auths concurrently with the trader.

Throttle budget (api_helper caps low-priority lane at 4 calls/sec, 120/min;
global cap is 10/sec, 170/min):
  10 contracts / 15s cycle = 0.67 calls/sec sustained.
  Start-of-cycle burst paced by local lane to 4/sec (drains in ~2.5s).
  During 09:15–15:30 overlap with paper trader (separate process — throttle
  state is NOT shared) the combined server-side load stays well under the
  10/sec global cap. Do not lower POLL_INTERVAL_SEC below 10 without
  re-deriving the overlap budget.
"""

from __future__ import annotations

import argparse
import csv
import logging
import signal
import sys
import time as time_module
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable, Iterable

import pytz
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api_helper import ShoonyaApiPy  # noqa: E402

IST = pytz.timezone("Asia/Kolkata")

LIQUID_5 = ("GOLD", "SILVER", "CRUDEOIL", "COPPER", "NATURALGAS")
SESSION_START_IST = time(9, 0)
SESSION_END_IST = time(23, 30)
POLL_INTERVAL_SEC = 15  # see throttle-budget note in module docstring

TICK_FIELDS = (
    "timestamp",
    "symbol",
    "instrument",
    "ltp",
    "volume",
    "bid",
    "ask",
    "oi",
    "bid_qty",
    "ask_qty",
    "expiry",
    "lot_size",
)


def parse_mcx_master(path: Path) -> list[dict]:
    """Parse symbols/MCX.csv. Returns one dict per row.

    MCX schema differs from NFO (extra `GNGD` column at index 3); this parser
    is MCX-specific by design — do not generalise.
    """
    rows: list[dict] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        if header[:8] != [
            "Exchange",
            "Token",
            "LotSize",
            "GNGD",
            "Symbol",
            "TradingSymbol",
            "Expiry",
            "Instrument",
        ]:
            raise ValueError(f"MCX.csv schema changed; got header: {header}")
        for r in reader:
            if len(r) < 8 or not r[0]:
                continue
            rows.append(
                {
                    "exchange": r[0],
                    "token": r[1],
                    "lot_size": int(r[2]) if r[2] else 0,
                    "symbol": r[4],
                    "trading_symbol": r[5],
                    "expiry": r[6],
                    "instrument": r[7],
                }
            )
    return rows


def select_front_two(
    rows: Iterable[dict],
    underlyings: Iterable[str],
    today: date,
) -> list[dict]:
    """For each underlying, return the two earliest non-expired FUTCOM contracts."""
    targets = set(underlyings)
    by_underlying: dict[str, list[dict]] = {u: [] for u in targets}
    for r in rows:
        if r["instrument"] != "FUTCOM" or r["symbol"] not in targets:
            continue
        try:
            exp = datetime.strptime(r["expiry"], "%d-%b-%Y").date()
        except ValueError:
            continue
        if exp < today:
            continue
        by_underlying[r["symbol"]].append({**r, "expiry_date": exp})
    selected: list[dict] = []
    for u in underlyings:
        contracts = sorted(by_underlying.get(u, []), key=lambda x: x["expiry_date"])
        selected.extend(contracts[:2])
    return selected


def session_dir(root: Path, today: date) -> Path:
    """Per-day output dir, mirroring the existing data_collector layout."""
    d = root / f"market_data_{today.strftime('%Y%m%d')}" / "raw_data" / "futures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_tick(out_dir: Path, contract: dict, quote: dict, ts_ist: datetime) -> None:
    """Append one tick row to MCX_<tradingsymbol>_YYYYMMDD.csv."""
    fname = f"MCX_{contract['trading_symbol']}_{ts_ist.strftime('%Y%m%d')}.csv"
    fpath = out_dir / fname
    new_file = not fpath.exists()
    row = {
        "timestamp": ts_ist.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "symbol": contract["trading_symbol"],
        "instrument": contract["instrument"],
        "ltp": float(quote.get("lp", 0) or 0),
        "volume": int(quote.get("v", 0) or 0),
        "bid": float(quote.get("bp1", 0) or 0),
        "ask": float(quote.get("sp1", 0) or 0),
        "oi": int(quote.get("oi", 0) or 0),
        "bid_qty": int(quote.get("bq1", 0) or 0),
        "ask_qty": int(quote.get("sq1", 0) or 0),
        "expiry": contract["expiry"],
        "lot_size": contract["lot_size"],
    }
    with open(fpath, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TICK_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def in_session(now_ist: datetime) -> bool:
    """MCX session: 09:00–23:30 IST, Mon–Fri. No weekends."""
    if now_ist.weekday() >= 5:
        return False
    t = now_ist.time()
    return SESSION_START_IST <= t <= SESSION_END_IST


def poll_once(
    api,
    contracts: list[dict],
    out_dir: Path,
    log: logging.Logger,
    now_ist: datetime,
) -> int:
    """One pass over all contracts. Returns count of successful ticks."""
    ok = 0
    for c in contracts:
        try:
            q = api.get_quotes(
                exchange=c["exchange"],
                token=c["token"],
                priority="low",
                context="mcx-collector",
            )
        except Exception as e:
            log.warning("get_quotes failed for %s: %s", c["trading_symbol"], e)
            continue
        if not q or "lp" not in q:
            continue
        append_tick(out_dir, c, q, now_ist)
        ok += 1
    return ok


def init_api(creds_path: Path, log: logging.Logger) -> ShoonyaApiPy:
    """Reuse cached cred.yml::Access_token. Exit cleanly on auth failure —
    do NOT re-auth concurrently with main.py."""
    with open(creds_path) as f:
        creds = yaml.safe_load(f)
    token = str(creds.get("Access_token", "")).strip()
    if not token:
        raise SystemExit("no cached Access_token in cred.yml; run main.py first to refresh")
    uid = str(creds.get("UID", "")).strip()
    account_id = str(creds.get("Account_ID", "")).strip() or uid
    host = str(creds.get("oauth_api_host", "")).strip() or "https://api.shoonya.com/NorenWClientAPI/"
    ws = str(creds.get("oauth_ws_endpoint", "")).strip() or "wss://api.shoonya.com/NorenWS/"
    api = ShoonyaApiPy()
    api.configure_oauth_service_host(host, ws)
    api.inject_oauth_header(token, uid, account_id)
    if not api.validate_oauth_session():
        detail = api.get_last_broker_error() or "unknown"
        raise SystemExit(f"cached token invalid ({detail}); run main.py to re-auth, then restart collector")
    log.info("OAuth session validated; collector starting")
    return api


def run(
    api,
    contracts: list[dict],
    repo_root: Path,
    log: logging.Logger,
    *,
    interval: float = POLL_INTERVAL_SEC,
    now_fn: Callable[[], datetime] = lambda: datetime.now(IST),
    sleep_fn: Callable[[float], None] = time_module.sleep,
    max_iterations: int | None = None,
) -> None:
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    iters = 0
    while not stop["flag"]:
        now_ist = now_fn()
        if not in_session(now_ist):
            log.info("outside MCX session at %s; exiting", now_ist.strftime("%Y-%m-%d %H:%M:%S"))
            return
        out_dir = session_dir(repo_root, now_ist.date())
        n = poll_once(api, contracts, out_dir, log, now_ist)
        log.debug("polled %d/%d contracts at %s", n, len(contracts), now_ist.strftime("%H:%M:%S"))
        iters += 1
        if max_iterations is not None and iters >= max_iterations:
            return
        sleep_fn(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MCX liquid-5 tick collector")
    p.add_argument("--cred", default=str(REPO_ROOT / "cred.yml"))
    p.add_argument("--master", default=str(REPO_ROOT / "symbols" / "MCX.csv"))
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    p.add_argument("--interval", type=float, default=POLL_INTERVAL_SEC)
    p.add_argument("--max-iterations", type=int, default=None, help="cap loop count (smoke testing)")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s [mcx] %(message)s",
    )
    log = logging.getLogger("mcx_collector")

    today = datetime.now(IST).date()
    rows = parse_mcx_master(Path(args.master))
    contracts = select_front_two(rows, LIQUID_5, today)
    if len(contracts) != 2 * len(LIQUID_5):
        log.warning(
            "expected %d contracts, got %d — some underlyings missing front-2",
            2 * len(LIQUID_5),
            len(contracts),
        )
    for c in contracts:
        log.info("tracking %s expiry=%s lot=%d token=%s", c["trading_symbol"], c["expiry"], c["lot_size"], c["token"])

    if contracts and args.interval > 0:
        sustained = len(contracts) / args.interval
        log.info(
            "throttle budget: %d contracts / %.0fs = %.2f calls/sec sustained (low-lane cap 4/sec)",
            len(contracts),
            args.interval,
            sustained,
        )
        if sustained > 4.0:
            log.warning("sustained rate exceeds low-lane cap; raise --interval")

    api = init_api(Path(args.cred), log)
    run(
        api,
        contracts,
        Path(args.repo_root),
        log,
        interval=args.interval,
        max_iterations=args.max_iterations,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
