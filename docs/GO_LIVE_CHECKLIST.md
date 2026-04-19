# Go-Live Checklist — Solo / Laptop Operation

Context: single operator, running on one MacBook, Shoonya broker, paper mode currently at `PAPER_TRADE_MODE=True`. Target: transition to live execution safely.

**Status of this document.** This is an advisory checklist, not a description of implemented controls. As of this writing, none of the following are present in the runtime: `LiveOrderManager`, startup reconciliation against broker state, order-status polling, pre-entry margin check via `get_limits()`, daily rupee loss cap, `data/HALT` kill switch, or `data/regimetrader.pid` single-instance guard. Every item below is work to be done, not verified behavior.

## Economic constraint: 10-lot minimum

The IC harvest model (`IC_HARVEST_PCT = 0.01`, 1% of max profit) doesn't survive smaller sizes once real brokerage is applied.

Rough math at current settings:

- `IC_MIN_CREDIT = 18.0`, `NIFTY_LOT_SIZE = 65`
- Max profit at 1 lot ≈ ₹18 × 65 = ₹1,170; 1% harvest ≈ ₹11.70
- Real brokerage + STT + GST on an 8-order round trip ≈ ₹150–₹200
- Net at 1 lot: **loss per trade** even when the strategy "wins"
- At 10 lots: max profit ≈ ₹11,700, 1% harvest ≈ ₹117 per cycle — still tight but workable because harvests repeat

Consequence: we cannot do a "1-lot proving period." The proving period must run at the live target size. That raises the bar on items 1–7 below — there is no cheap learning phase.

---

## Must-do before go-live (core build)

1. **Live order path.** `main.py` wires only the `Paper*` managers. Build a `LiveOrderManager` that mirrors `PaperOrderManager`'s interface. The strategy's actual contract is small: `iron_condor.py` only calls `place_order(symbol, side, qty, track_position=?)` and `build_option_symbol(instrument, expiry, strike, kind)`. Match those two methods and the existing return shape (`{"status": "COMPLETE" | "REJECTED", "fill_price": ...}`) and no changes are needed in `iron_condor.py`. `cancel_order` is not currently on the interface; add it if the live polling loop (item 3) needs to cancel timed-out pending legs, but it is not required for parity with paper.

2. **Startup reconciliation.** On launch, call broker `get_positions` and `get_order_book`, rebuild `PositionTracker` from that — not from `data/open_positions.json`. Treat the JSON as a hint; broker is source of truth. Prevents ghost positions after crash/restart.

3. **Order-status polling.** Paper mode assumes instant fills. Live mode must poll `single_order_history` until status resolves to `COMPLETE` / `REJECTED` / `CANCELED`, with a per-leg timeout (e.g., 30 s). No silent assumptions.

4. **Rollback escalation.** Today `_rollback_partial_entry` sends reverse orders. In live, the reverse order can also fail (circuit, margin, illiquid). If rollback itself fails, halt the system, flag the stuck legs, and alert — do not continue. A stuck half-condor is the worst-case scenario at 10 lots.

5. **Pre-entry margin check.** Call `get_limits()` before leg 1 and confirm available margin covers all four legs + buffer. Broker rejecting leg 3 after legs 1–2 filled is exactly the situation rollback was built for — avoid triggering it by checking upfront.

6. **Daily rupee loss cap.** Add `DAILY_MAX_LOSS` (pick a number you can actually stomach at 10 lots on both instruments — likely ₹50k–₹100k). When `daily_realised + unrealised < -DAILY_MAX_LOSS`, halt entries and flatten. This is independent of the 3× max-profit multi-leg stop.

7. **Kill switch file.** Top of the main loop: if `data/HALT` exists, force-exit everything and exit the process. Now any terminal can stop trading with `touch data/HALT` — no signal-handling fragility.

## Before first real trade

8. **Fix or prune broken tests, and fix the test packaging.** Two separate issues:
   - *Import failures from dead modules:* `test_paper_trading.py` and `test_position_persistence.py` import the missing `trading_system.core.strategy_a`; `test_integration.py` imports the missing `trading_system.core.daily_target`. Either restore those modules or drop the tests. They are already excluded via `tests/conftest.py::collect_ignore`, but that just hides the rot — resolve one way or the other.
   - *Packaging gap:* there is no `pyproject.toml`, `setup.py`, or `pytest.ini`, and nothing adds the project root to `sys.path` globally. Whether a given test file can import `trading_system` depends on whether that file self-patches `sys.path` (e.g. `test_pnl_engine.py:3` does; `test_ic_strategy.py` does not). Targeted invocations like `pytest tests/test_ic_strategy.py` currently fail at collection with `ModuleNotFoundError: No module named 'trading_system'` unless run with `PYTHONPATH=.` or similar. Add a minimal `pyproject.toml` (or a `pytest.ini` with `pythonpath = .`) so any invocation works without ad-hoc path tricks. This matters more once live code is added and you want to run targeted tests.
   - After both are fixed, `test_ic_strategy::test_ic_strategy_entry_success` still fails on a MagicMock-return-shape issue in `enter()` — investigate that on its own merits, not as part of the packaging fix.

9. **Phone alerts.** One integration only — Telegram bot or `ntfy.sh` (free, no auth setup). Alert on: stop-loss hit, entry rejection, auth failure, any unhandled exception in the main loop, rollback escalation from item 4. ~30 lines of code.

10. **Heartbeat.** A `launchd` job that checks `mtime` of `data/pnl_snapshot.json` every 5 min during market hours (09:15–15:30 IST). If stale > 3 min, ping your phone. Catches silent process death.

## Proving period (adapted for 10-lot minimum)

11. **One instrument, 10 lots, two weeks.** Since we can't go smaller, reduce the other dimension: run NIFTY only (or BANKNIFTY only) for ~10 trading days at `IC_LOT_SIZE=10`. Comment out the other `IronCondorStrategy` instantiation in `main.py`. Half the exposure, same per-trade economics.

12. **Nightly reconciliation.** Each evening, pull Shoonya contract notes for the day and diff against `data/paper_trades.csv` rows and `pnl_snapshot.json`. If broker-vs-engine PnL diverges by more than ~2% on any single trade, find the bug before the next session. Likely sources of divergence, in order:
    - Slippage model (`paper_order_manager.py` applies a percentage + OTM multiplier; live fills will differ)
    - Brokerage rate (currently `BROKERAGE_PER_ORDER = 5.0` flat; Shoonya tier may differ — set this to your actual per-order brokerage before going live)
    - **GST is not modeled in the paper layer.** The engine applies STT and flat brokerage only. Once live, GST (18% on brokerage + transaction charges + SEBI fees) will appear as a systematic negative delta against paper. Either add GST to the paper cost model before go-live, or account for it as a known residual during reconciliation.
    - Exchange transaction charges and SEBI fees — also not in the paper model; same treatment as GST.

13. **Only enable both instruments after reconciliation is clean for a full week.** Don't scale until the single-instrument run is boring.

## Laptop-specific operational gotchas

14. **Keep the Mac awake.** `caffeinate -di` in a dedicated terminal while the process runs, or use Amphetamine. A sleeping laptop at 14:14 IST with open condors on both indices = catastrophic.

15. **Power + network.** Plug in. Know your phone hotspot password. Shoonya has maintenance windows — check their status page before session start.

16. **Single-instance guard.** Add a PID-file check in `main.py` startup: if `data/regimetrader.pid` exists and that PID is alive, refuse to start. A second accidental `./start.sh` would place duplicate orders.

17. **Credential hygiene.** `cred.yml` is git-ignored — keep it that way. Don't paste tokens into chat, screenshots, or commits. Consider moving to macOS Keychain later; not a day-one blocker for solo use.

## Shortest realistic path

- Items 1–7: ~1 week of focused build (live order manager is the bulk of it)
- Items 8–10: ~1 evening
- Items 11–13: 2 weeks of real-money observation, single instrument
- Total: **~3 weeks** from today to both-instrument live operation at 10 lots

## Explicitly deferred (don't need on day one)

- Multi-environment config (dev/staging/prod) — one laptop, one config
- Process supervisor like systemd — manual restart is fine for solo
- Secret vaults — `cred.yml` is adequate for a single operator
- Chaos/fault-injection testing — proving period is your fault injection
- SEBI algo-trading registration — check with broker if the request rate triggers their threshold; likely fine at this frequency

## Open questions to decide before coding

- What is your `DAILY_MAX_LOSS` tolerance in absolute rupees?
- Start with NIFTY only or BANKNIFTY only for the proving period?
- Shoonya's actual brokerage tier for your account (affects item 12 reconciliation tolerance)?
