# Go-Live Checklist — Solo / Laptop Operation

Context: single operator, running on one MacBook, Shoonya broker, paper mode currently at `PAPER_TRADE_MODE=True`. Target: transition to live execution safely.

**Status of this document.** This is an advisory checklist + a trackable backlog, not a description of implemented controls. The `LIVE-nn` entries below are individually addressable items; fixing each ships with a regression test (per working-style convention).

## Progress

**23 of 26 in-scope items addressed.** Fully: LIVE-01, LIVE-07, LIVE-08, LIVE-12, LIVE-13, LIVE-18, LIVE-19, LIVE-20, LIVE-21, LIVE-22, LIVE-23, LIVE-24, LIVE-26, LIVE-27, LIVE-28, LIVE-29, LIVE-30. Via-LIVE-25 (active under `IC_ENTRY_MODE="hedge_first"`): LIVE-02, LIVE-03 (narrowed), LIVE-06 (wired as LIVE-25 consumer), LIVE-25. LIVE-05 addressed (skip-cycle-with-cap policy). Paper-side auth recovery is tracked as `bugs_for_review.md::BUG-07`. LIVE-09 and LIVE-17 deferred 2026-04-26 — see "Explicitly deferred" below.

| Priority | Open | Addressed | Deferred |
|---|---|---|---|
| P0 | — | LIVE-01, LIVE-02, LIVE-03, LIVE-06, LIVE-07, LIVE-10, LIVE-13, LIVE-19, LIVE-20, LIVE-21, LIVE-22, LIVE-25 | — |
| P1 | LIVE-04, 14 | LIVE-05, LIVE-08, LIVE-11, LIVE-12, LIVE-18, LIVE-23, LIVE-24 | — |
| P1 (operational tuning) | — | LIVE-26, LIVE-27, LIVE-28, LIVE-29, LIVE-30 | — |
| P2 | — | — | LIVE-09, LIVE-17 |

Severity scale:
- **High** — can create uncontrolled exposure, silently wrong P&L in live, or block the go-live decision itself.
- **Medium** — correct direction is known but unmodeled; creates systematic bias or latency.
- **Low** — cleanup / hygiene / latent trap.

Priority scale:
- **P0** — must land before any real-money order leaves the process.
- **P1** — must land before the proving period can produce a trustworthy result.
- **P2** — address opportunistically; not a blocker.

Axioms referenced throughout: see `docs/axioms.md`. Paper-side runtime defects are tracked separately in `docs/bugs_for_review.md`.

## Economic constraint: 10-lot minimum

The IC harvest model (`IC_HARVEST_PCT = 0.01`, 1% of max profit) doesn't survive smaller sizes once real brokerage is applied.

Rough math at current settings:

- `IC_MIN_CREDIT = 18.0`, `NIFTY_LOT_SIZE = 65`
- Max profit at 1 lot ≈ ₹18 × 65 = ₹1,170; 1% harvest ≈ ₹11.70
- Real brokerage + STT + GST on an 8-order round trip ≈ ₹150–₹200
- Net at 1 lot: **loss per trade** even when the strategy "wins"
- At 10 lots: max profit ≈ ₹11,700, 1% harvest ≈ ₹117 per cycle — still tight but workable because harvests repeat

Consequence: we cannot do a "1-lot proving period." The proving period must run at the live target size. That raises the bar on every P0 item below — there is no cheap learning phase.

---

# Backlog

## Category 1 — Order lifecycle

### LIVE-01 · Synchronous order completion is assumed
- **Status:** Addressed 2026-04-24 — `trading_system/live/live_order_manager.py::LiveOrderManager.place_order` blocks until the order reaches a terminal status (`COMPLETE` / `REJECTED` / `CANCELED`) by polling `single_order_history` with exponential-backoff retry (raises `OrderPollingAbandoned` after `MAX_POLL_ERRORS` consecutive API errors). Public interface is identical to `PaperOrderManager.place_order`, so `IronCondorStrategy` is mode-unaware. `main.py` wiring flipped from an import-time `if PAPER_TRADE_MODE` branch (which resolved once against the default at module load and made the live path unreachable under monkey-patched tests) to a runtime `_build_order_stack(api, md, trade_logger)` helper that selects the class at construction time. `PaperPositionTracker`/`PaperPnLEngine` are kept as shared state containers (mode-agnostic despite the name) — only the order manager swaps. Two downstream gates un-gated as part of the wiring: the cycle-end `pnl_engine.write_snapshot()` and the EOD snapshot write are now mode-agnostic (LIVE-24 heartbeat watches freshness and would starve in live otherwise). Secondary safety win: `pnl_engine.record_trade(...)` and the LIVE-22 daily loss-cap check, which previously would have `NameError`'d in live because `pnl_engine=None`, now actually run. 2 new regression tests in `tests/test_main_helpers.py` pin `_build_order_stack`'s construction and tracker-threading for both modes; full suite 371 green (up from 369). `OrderPollingAbandoned` propagates through `iron_condor.py` (no `try/except` wraps any `place_order` call there) to `main.py`'s cycle-level halt-on-exception handler, which halts, alerts, and persists state — same fate as any other unhandled exception per Axiom 3.
- **Severity:** High
- **Severity reason:** The entire IC entry/exit/rollback control flow depends on `place_order()` returning a terminal status on the same call. Shoonya's live API returns orders in `PENDING`/`OPEN` and resolves asynchronously via order-update messages or polling — so today's control flow cannot correctly drive live orders.
- **Priority:** P0
- **Priority reason:** Foundational shape mismatch between paper and live. Every other lifecycle gap (LIVE-02, LIVE-03) is downstream.
- **Evidence:**
  - `trading_system/paper/paper_order_manager.py:179-199` — `place_order` synthesises a `{"status": "COMPLETE", ...}` dict inline and returns it before the function exits.
  - `trading_system/core/iron_condor.py:244-257` — entry loop checks `order.get("status") != "COMPLETE"` immediately and triggers rollback on anything else.
  - `trading_system/core/iron_condor.py:361-367` — exit loop uses the same synchronous shape.
- **Impact:** A naive `LiveOrderManager` that forwards to Shoonya would return `PENDING` from `place_order` and the strategy would immediately classify leg 1 as rejected and start rollback — rolling back orders that are still on the way to being filled.
- **Suggested approach:** Introduce a per-leg `await_terminal_status(order_id, timeout)` polling `single_order_history` until status resolves to `COMPLETE` / `REJECTED` / `CANCELED`. Favour blocking inside `place_order` for interface parity with paper — the strategy contract is small. Regression test: fake broker returning `PENDING` → `COMPLETE` across two polls; assert entry proceeds, not rolls back.

### LIVE-02 · Atomic 4-leg entry assumes no price drift or partial fills
- **Status:** Addressed-via-LIVE-25 (2026-04-23) — Phase 5a of `enter_hedge_first` recomputes net credit from actual fill prices of all 4 legs and unwinds everything if `< IC_MIN_CREDIT`. Becomes the active path when `settings.IC_ENTRY_MODE="hedge_first"`.
- **Severity:** High
- **Severity reason:** The `net_credit_unit` that gates entry is computed from LTPs fetched before leg 1. In live, the 4 legs take non-zero time; prices drift and liquidity gets consumed, so legs 3–4 can fill at materially different prices than quoted — or not at all.
- **Priority:** P0
- **Priority reason:** Axiom 4 requires all four legs confirmed before the IC is considered to exist. Today the system uses a pre-trade price snapshot as proxy for post-trade reality.
- **Interaction with LIVE-25:** Becomes LIVE-25's Phase 5a gate. Same post-fill credit re-check logic; new home in the phased entry flow — after both shorts fill, confirm `net_credit ≥ IC_MIN_CREDIT` using actual fill prices of all four legs; if not, abort via the Phase 5b unwind path.
- **Evidence:**
  - `trading_system/core/iron_condor.py:195-213` — `prices = {...}` snapshot + credit gate run once.
  - `trading_system/core/iron_condor.py:243-257` — legs placed sequentially; no re-check of credit after fills.
- **Impact:** Three failure modes in live: (1) all four fill but combined credit drops below `IC_MIN_CREDIT`; (2) leg 3 or 4 is rejected for margin/circuit/liquidity (triggers LIVE-03); (3) legs partial-fill (LIVE-05).
- **Suggested approach:** After all legs terminal-resolve, recompute post-fill credit. If below `IC_MIN_CREDIT` beyond a configurable tolerance, close the position through the normal exit path rather than carrying a structurally-worse IC. Regression test: simulate legs 1–2 filling 10% worse than quoted; assert post-fill credit check triggers close.

### LIVE-03 · Rollback reverse orders can fail in live, leaving naked short options
- **Status:** Narrowed-by-LIVE-25 (2026-04-23) — under `IC_ENTRY_MODE="hedge_first"` the unbounded-naked-short scenario is eliminated by construction: wings are on before any short leg goes out. Phase 2's one-wing-fails path and Phase 5b's short-timeout path both unwind cleanly with bounded loss. The original LIVE-03 "mid-rollback hedge" lives in the legacy sequential path only; once hedge-first is the default, that path and its LIVE-03 exposure surface go away.
- **Severity:** High
- **Severity reason:** The most dangerous failure mode at 10 lots. The two short legs (SC, SP) are uncovered without their wings. If the rollback's reverse BUY fails (circuit, margin call triggered by the first leg's margin consumption, illiquid far-OTM), exposure is real, directional, and unhedged.
- **Priority:** P0
- **Priority reason:** No live-money use is acceptable until rollback failure is not just escalated but actively hedged. `RiskManager.escalate_rollback_failure` already halts entries; it does not unwind the stuck legs.
- **Interaction with LIVE-25:** If LIVE-25 (hedge-first entry sequencing) ships first, the unbounded-naked-short scenario is eliminated by construction — wings are already on before any short leg goes out. LIVE-03 then narrows to the edge case of one wing filling but not the other, which is still possible but bounded, not catastrophic. Sequence LIVE-25 before LIVE-03's hedge implementation.
- **Effective severity post-LIVE-25:** Medium. Once LIVE-25 ships, the only reachable failure is "one wing fills, the other doesn't" — yielding a single long option position with max loss bounded at `wing_premium × wing_qty` (roughly ₹3–10k at 10 lots NIFTY). Treat as cleanup path, not catastrophic exposure. Severity/Priority in this header reflect the pre-LIVE-25 state for audit continuity.
- **Evidence:**
  - `trading_system/core/iron_condor.py:62-103` — `_rollback_partial_entry` submits reverse orders and records `stuck_legs` on failure; no hedge placed.
  - `trading_system/core/risk_manager.py:83-99` — `escalate_rollback_failure` sets `halted = True` and logs; halt does not remove the stuck exposure.
  - `trading_system/paper/paper_order_manager.py:117-199` — paper reverse orders always succeed.
- **Impact:** At 10 lots NIFTY (650 qty) short 1 CE strike with no corresponding long CE, an adverse 100-point move is ₹65k of loss per strike per leg, uncapped.
- **Suggested approach:** On rollback failure, attempt a protective hedge at the nearest liquid strike (cheapest available wing) to cap exposure, then halt. If the hedge also fails, emit an operator alert (LIVE-23) and persist the stuck set to `data/open_positions.json::stuck_legs`. Next startup must refuse to trade until an operator clears. Regression test: inject a rollback reverse-order rejection; assert a hedge order is attempted and an operator-alert path fires.

### LIVE-25 · Hedge-first IC entry sequencing
- **Status:** Implemented (2026-04-23) — `IronCondorStrategy.enter_hedge_first()` ships the full 5-phase state machine: Phase 1 wings as MKT, Phase 2 both-full-or-unwind (including partial-fill halt-and-unwind per LIVE-05), Phase 3 credit-feasibility check + refuse-and-unwind, Phase 4 shorts as LMT at bid, Phase 5a post-fill credit re-check (absorbs LIVE-02), Phase 5b unwind-all on short timeout. Consumes `MarketData.get_quote_book()` (LIVE-06). Paper-side LMT order support landed alongside (`PaperOrderManager.place_order` now honors `price_type="LMT"`). 12 state-machine tests + 8 paper-LMT tests. 285 passing. **Still open:** flip `enter()` to dispatch to `enter_hedge_first` under `IC_ENTRY_MODE="hedge_first"` setting, then flip default after proving period.
- **Severity:** High
- **Severity reason:** Risk-profile-changing. Current implicit entry order treats all four legs symmetrically as market orders, which means any leg failure after a short leg fills produces unbounded naked-short exposure (the LIVE-03 scenario). A hedge-first ordering — buy the two long wings first as market orders, then sell the two short legs as limit orders priced off actual wing fills — converts the worst-case failure from *unbounded naked short* to *own a long strangle capped at premium paid*. Bounded vs unbounded is categorical, not incremental.
- **Priority:** P0
- **Priority reason:** Foundational to the live entry design. Landing LIVE-25 first materially simplifies LIVE-03 and strengthens LIVE-02. Deferring it means building LIVE-03's catastrophic-scenario hedge logic against a risk that this design eliminates by construction.
- **Evidence:**
  - `trading_system/core/iron_condor.py:316` — `place_order(symbol, side, qty)` called sequentially per leg with no `price_type`, so all legs go as `MKT` (the `paper_order_manager.place_order` default at `trading_system/paper/paper_order_manager.py:113`).
  - `trading_system/core/iron_condor.py:195-213` — entry credit is computed once upfront from `get_ltp`; there is no notion of deriving short-leg prices from actual wing fills.
  - No existing code supports limit orders with `price` parameter + terminal-status awaiting + cancel-on-timeout — this is new plumbing on top of LIVE-01.
- **Impact:** Without LIVE-25, the strategy's worst-case live failure is unbounded. With LIVE-25, the worst case is bounded at roughly `(ask − bid) × wing_qty + wing_premium × wing_qty` ≈ ₹10k–₹20k for a 10-lot NIFTY entry — losable, not catastrophic. The scale of risk reduction justifies the additional state-machine complexity.
- **Suggested approach:**
  - Phase 1: submit LC + LP as `MKT` in parallel (async plumbing from LIVE-01).
  - Phase 2: await both terminal. If one wing fills and the other fails, close the filled wing at market and halt (this is the narrowed LIVE-03 path).
  - Phase 3: compute short-leg target limits from actual wing fills — `required_total_credit = IC_MIN_CREDIT + LC_fill + LP_fill`, split proportionally across SC and SP by current LTP.
  - Phase 4: submit SC + SP as `LMT` orders at those targets, IOC or with a configurable timeout.
  - Phase 5a: both short legs fill → post-fill credit re-check (LIVE-02) confirms ≥ `IC_MIN_CREDIT` net; entry complete.
  - Phase 5b: one or both shorts don't fill within timeout → cancel remaining, close any filled short at market, close the wings at market, abort. Log reason so Phase 4's timeout/offset can be tuned.
  - New settings (locked 2026-04-23): `IC_SHORT_LIMIT_TIMEOUT_SEC=600`, `IC_SHORT_LIMIT_OFFSET_TICKS=0`, `IC_PHASE3_FALLBACK="refuse"`.
  - **Caching note for `get_quote_book` consumers:** `MarketData.get_quote_book` (landed 2026-04-23) has no internal TTL today. Phase 1 and Phase 4 both call it per-leg; decide upfront whether to (a) add a short TTL inside `get_quote_book` mirroring `get_ltp`'s 2s cache, or (b) fetch once per leg in `enter()` and pass the snapshot down. Avoid burning Shoonya rate-limit headroom (LIVE-17).
  - Order type support: extend `place_order` (paper + live) to accept `price_type="LMT"` with `price` and a terminal-status awaiter that handles `CANCELED` on timeout.
  - Regression tests: (a) happy-path all four fill, assert Phase 3 limit prices match computed formula within 1 tick; (b) one long fails → other wing closed at market, no shorts submitted; (c) shorts timeout → wings closed, abort path fires with bounded loss logged; (d) Phase 3 computation on hostile inputs — wings cost more than IC_MIN_CREDIT allows → entry refused with a structured reason; (e) short partial fill → cancel remainder + unwind (preserves 10-lot axiom).

## Category 2 — Fill model

### LIVE-04 · Slippage model understates far-OTM wing fills
- **Status:** Open
- **Severity:** Medium
- **Severity reason:** Paper applies `max(ltp * 0.05%, ₹0.25)` (tripled for legs under ₹50). Real bid/ask on weekly far-OTM NIFTY/BANKNIFTY options regularly runs ₹1–₹5 wide. A 10-lot market order walks that book.
- **Priority:** P1
- **Priority reason:** Does not break paper correctness, but makes paper win-rate and P&L systematically optimistic — directly enabling LIVE-21.
- **Evidence:**
  - `trading_system/paper/paper_order_manager.py:166-175` — slippage math.
  - `trading_system/config/settings.py:100-105` — `SLIPPAGE_PCT=0.0005`, `SLIPPAGE_MIN_ABS=0.25`, `SLIPPAGE_OTM_THRESHOLD=50.0`.
- **Impact:** Paper profit estimates are optimistic by roughly ₹(wing_spread × lots × lot_size × 4 legs) per round-trip beyond the modelled slippage — easily several hundred rupees per harvest cycle. The harvest target becomes net-negative faster than paper suggests.
- **Suggested approach:** During the proving period, collect per-leg broker fill prices vs quoted LTP and fit an empirical slippage model keyed off `(moneyness, VIX_tier, side)`. Apply that model to paper as a calibrated floor. Regression test: inject a broker fill at LTP + ₹3 on a short leg priced at ₹20; assert paper model's calibrated slippage lies within 25% of observed.

### LIVE-05 · Partial fills are not handled
- **Status:** Addressed 2026-04-24, refined 2026-04-27 (harvest carve-out), unified 2026-04-28 (skip-cycle-with-cap).
  - Both entry paths still unwind partial-filled shorts by their actual `fill_qty`, not the requested `qty` (the original LIVE-05 invariant — over-reversing leaves net-LONG exposure on the short strike). That part is unchanged.
  - Halt policy on partial-fill EVENT, hedge-first `enter_hedge_first()` Phase 5b: a single partial-fill skips the cycle and increments `_consecutive_partial_fails`; only `settings.IC_PARTIAL_FAIL_CAP` (default 3) consecutive partial-fills halt, with reason code `consecutive_partial_fail_cap_reached`. Counter resets on a successful entry. Bid drift between QuoteBook fetch and order submission is normal microstructure noise (incidents 2026-04-27 12:00 harvest re-entry and 2026-04-28 10:49 fresh BANKNIFTY entry both showed identical `SELL LMT > LTP → CANCELED` mechanism); halting on a single such event cost ~₹1,200 of unwind plus all forward expected value per occurrence. The cap bounds the unwind-slippage tail when liquidity IS genuinely stressed.
  - Halt policy unchanged: legacy `enter()` (inactive in production with `IC_ENTRY_MODE="hedge_first"`) still escalates on any partial-fill via `_handle_partial_fill_halt`'s `partial_fill_during_entry` sentinel. Phase-1/4 OrderPollingAbandoned (broker-connectivity events, distinct signal) still halt unconditionally. Unwind-order failures (genuine stuck legs in the book) still halt via `_drain_rollback_failures` → `escalate_rollback_failure`.
  - Phase-3 short-leg re-quote (added 2026-04-28): the top-of-function QuoteBook fetch is ~200-2000ms stale by the time Phase 3 runs (Phase 1+2 wing latency in live). The Phase-3 SC/SP re-fetch immediately before computing limits closes the stale-window down to ~50-200ms (Phase-3 → Phase-4 latency). Cuts partial-fail rate without altering the cap policy. Re-fetch returning untradable → abort with wing unwind via the existing refuse path.
  - Clean non-fills (`fill_qty=0` on both shorts) intentionally do NOT increment the counter — wings unwind cleanly and the next cycle retries.
  - Regression tests in `tests/test_partial_fill_halt.py` and `tests/test_ic_hedge_first.py` pin: skip-cycle-no-halt on single partial, halt-on-cap with the unified reason code, counter reset on successful entry, fill_qty-not-qty reversal invariant, no-escalate-on-clean-nonfill boundary, and legacy `enter()` halt-on-partial behavior.
- **Severity:** Medium
- **Severity reason:** Shoonya returns `COMPLETE` only when total quantity fills. A 10-lot (650-qty) order on a deep OTM leg can fill 300 and then stall. The tracker and strategy have no representation for this state.
- **Priority:** P1
- **Priority reason:** Low-probability on NIFTY ATM-ish wings; high-probability on far-OTM BANKNIFTY wings during volatile opens.
- **Evidence:**
  - `trading_system/paper/paper_position_tracker.py:27-65` — `add_position` consumes `order["quantity"]` whole; no partial-qty state.
  - `trading_system/core/iron_condor.py:246-257` — whole-order COMPLETE-or-not check.
- **Impact:** On a partial-then-cancel, the engine has already moved on. P&L and position state diverge from broker.
- **Suggested approach:** In `LiveOrderManager.await_terminal_status`, treat partial-then-cancel as a distinct status. If encountered mid-entry, treat as leg failure and trigger LIVE-03's hedge-and-halt path rather than rollback (rollback on a partial is ambiguous). Regression test: fake broker returning qty=650 order as partial-filled=300 then canceled; assert hedge-and-halt.

### LIVE-06 · LTP is not an executable price (bid/ask and size blindness)
- **Status:** Partial (2026-04-23) — `MarketData.get_quote_book(symbol_key)` + `QuoteBook` dataclass (bid/ask/bid_qty/ask_qty/mid/spread/is_tradable) added with 7 regression tests. Remaining work: wire into IC entry for (a) mid-based credit computation and (b) per-leg liquidity pre-check. Both consumers land naturally as part of LIVE-25's Phase 1 (wing liquidity) and Phase 4 (short-leg limit pricing).
- **Severity:** Medium
- **Severity reason:** `get_ltp` returns the last trade price. Short legs execute against the live bid; long legs against the live ask. On deep-OTM options, bid can be 0 with a non-zero last-trade, making the leg un-shortable at any price near LTP.
- **Priority:** P0
- **Priority reason:** Prerequisite for LIVE-25 Phase 4 — without real `bp1`/`sp1`/`bq1`/`sq1` from `get_quotes`, the short-leg limit prices in the hedge-first sequencing can't be set and liquidity pre-checks on the wings can't be performed. Promoted from P1 when LIVE-25 became the live entry design.
- **Evidence:**
  - `trading_system/existing/market_data.py:64-111` — `get_ltp` reads only `quote["lp"]`. Bid/ask fields are untouched (existing FixQ1 path at lines 116-117 already reads `bp1`/`sp1` as a fallback, so the API call shape is known to work).
  - `trading_system/core/iron_condor.py:195-209` — entry credit computed entirely off `lp`.
- **Impact:** Pre-entry credit check can pass on stale last-trade values the book cannot support. Entry fills at materially worse levels or fails outright.
- **Suggested approach:** Extend `MarketData` with `get_quote_book(symbol)` returning bid/ask/bid_qty/ask_qty. Pre-entry, reject any leg where `bid_qty < our_qty` on the short side or `ask_qty < our_qty` on the long side, with a configurable margin. Use `(bid+ask)/2` instead of `lp` for the credit computation. Regression test: craft a quote where `lp=20.0` but `bp1=0.0`; assert entry rejects on short-leg liquidity check.

## Category 3 — State vs broker truth

### LIVE-07 · No startup reconciliation against broker positions / order book
- **Status:** Addressed 2026-04-24 — `trading_system/ops/startup_reconcile.py` provides `reconcile_startup_positions(engine_positions, broker_positions) -> StartupReconciliationReport`, a pure function that surfaces every divergence kind (engine_only / broker_only / qty mismatch) in one structured report. Wired into `main.py` between the abnormal-exit guard and the main loop: in live mode only (gated on `not settings.PAPER_TRADE_MODE`), calls `api.get_positions()`, reconciles against `PaperPositionTracker._positions`, and on any divergence halts the process with `risk.halted=True` and fires a `startup_reconcile_divergent` critical alert via the LIVE-23 channel. Broker query exception is itself a halt condition (`startup_reconcile_failed`). Zero-qty Shoonya rows filtered as day-flat; malformed rows (missing `tsym`/`exch`, non-numeric `netqty`, non-dict) skipped defensively. Regression: 14 tests in `tests/test_startup_reconcile.py` covering phantom/hidden/partial-fill divergence, multi-discrepancy aggregation, zero-qty filter, malformed-row safety, and structured-summary grep tags. Open-order-book side of the spec deferred — positions are the dominant live-exposure risk; orders can follow once live shake-down surfaces a concrete need.
- **Severity:** High
- **Severity reason:** Today's startup restores state from `data/open_positions.json` and treats that as truth. In live, the broker is the only authoritative source. A crash between leg-2 fill and leg-3 send leaves the broker with 2 legs and the JSON with 0.
- **Priority:** P0
- **Priority reason:** Axiom 3 ("uncertain state halts entries until trustworthy") is violated on every cold-start in live without explicit reconciliation.
- **Evidence:**
  - `trading_system/core/position_persistence.py` — load path treats JSON as authoritative (called from `main.py:382`).
  - `main.py:370-390` — no `get_positions`/`get_order_book` call at startup.
- **Impact:** Phantom positions (JSON shows legs not at broker) or hidden positions (broker has legs JSON doesn't know). Both lead to incorrect monitoring, harvest decisions, and force-exit targeting.
- **Suggested approach:** On startup (live mode), call `get_positions` + `get_order_book`, reconstruct the `PositionTracker` from those, and compare against the JSON. Any divergence: halt, log a structured diff, require operator clear. JSON becomes audit artefact, not primary source. Regression test: seed JSON with a leg the fake broker doesn't have; assert startup halts with a structured diff in the log.

### LIVE-08 · No per-trade reconciliation against broker trade book
- **Status:** Addressed (2026-04-23, fully closed 2026-04-28) — `trading_system/ops/reconcile.py` library + `tools/reconcile_trades.py` CLI. Matches engine `paper_orders.csv` legs to broker contract-note CSV on `(symbol, side, time±window)` with tie-break by closest-in-time; emits `data/reconciliation_YYYYMMDD.json` with matched pairs (`price_delta`, `price_delta_pct`, `qty_delta`, `cost_delta`, `time_delta_sec`), unmatched engine legs (phantom fills), unmatched broker legs (rogue fills), and flagged rows where price drift > 2%. CLI exits non-zero on any drift so cron/launchd can alert without parsing JSON. Live-mode broker-fetcher shim landed 2026-04-28 in `trading_system/ops/broker_trade_fetcher.py` — `fetch_trade_book_legs(api, date_iso)` wraps `api.get_trade_book()` and normalises to `reconcile.Leg` using documented Shoonya field names (`tsym`, `exch`, `trantype`, `flqty`, `flprc`, `fltm`); the field map is the single point of update if a key shifts upstream. Cost columns emit zero (cost stack lives on the contract note, LIVE-12). 23 regression tests across `tests/test_reconcile.py` (10) and `tests/test_broker_trade_fetcher.py` (13) pin the field-name contract, date filter, malformed-row skip, and end-to-end pipeline. Day-1 of shakedown: validate the field map against a real `get_trade_book()` response — single point of update if it diverges. Wiring into the EOD flow (writes `data/reconciliation_YYYYMMDD.json`, starts LIVE-21's 10-day clock) is a separate follow-up commit.
- **Severity:** Medium
- **Severity reason:** Nothing today ties `paper_trades.csv` rows to broker contract notes. Fills logged in the engine may differ from what actually printed. Without per-trade reconciliation, divergence cannot be attributed to slippage (LIVE-04), fees (LIVE-12), or a real bug.
- **Priority:** P1
- **Priority reason:** The proving period's whole point is to expose model errors against reality. Without this it produces no falsifiable signal.
- **Evidence:**
  - `trading_system/core/trade_logger.py` — writes internal view only.
  - `trading_system/paper/paper_pnl_engine.py:62-111` — `record_trade` has no broker-side cross-check.
- **Impact:** Silent drift between engine and broker P&L. The 2% tolerance target below becomes uncheckable because there is no pipe to compute it.
- **Suggested approach:** Add a post-session script `tools/reconcile_trades.py` that pulls Shoonya contract notes for the day, joins by `(symbol, side, time±window)` to `paper_trades.csv`, and writes `data/reconciliation_YYYYMMDD.json` with per-trade deltas in price, qty, and realised P&L. Any `|delta_pnl| / |expected_pnl| > 2%` flagged. Regression test: contract-note fixture + CSV fixture, assert reconciliation diff matches expected deltas.

### LIVE-09 · Order tag IDs collide across process restarts
- **Status:** Deferred 2026-04-26 — out of go-live blocker scope. Tag collisions only complicate hand-reading of `paper_orders.csv` across restarts; LIVE-08 reconciliation matches on `(symbol, side, time±window)` and does not consume the tag. No safety property depends on this. Revisit opportunistically post-shakedown if reconciliation ergonomics warrant it.
- **Severity:** Low
- **Severity reason:** `PaperOrderManager._id_counter = itertools.count(1)` resets each run. `PAPER_1` can refer to different orders across sessions. In paper this muddies audit trail; in live it complicates broker-tag-based reconciliation.
- **Priority:** P2
- **Priority reason:** Hygiene; fix before first real trade but not on the critical path.
- **Evidence:**
  - `trading_system/paper/paper_order_manager.py:33` — `_id_counter = itertools.count(1)`.
  - `trading_system/paper/paper_order_manager.py:69-70` — `_next_id` returns `PAPER_N`.
- **Impact:** `paper_orders.csv` has duplicate IDs for distinct orders across runs. Live-reconciliation against broker tags becomes ambiguous.
- **Suggested approach:** Tag format `{mode}_{YYYYMMDD}_{seq}` where seq persists in a small counter file under `data/`. Regression test: two in-process managers started same day, assert second's first ID > first's last ID.

## Category 4 — Economics

### LIVE-10 · No pre-entry margin check
- **Status:** Addressed 2026-04-24 — `trading_system/core/margin.py::estimate_ic_required_margin(wing_width, lot_size, lots, net_credit_unit, buffer=1.2)` returns the max-loss × buffer upper bound. `get_available_margin()` added to both order managers: `PaperOrderManager` returns `float('inf')` (no-op in paper); `LiveOrderManager` wraps `api.get_limits()` and computes `cash - marginused`, failing-closed to `0.0` on any exception / malformed / non-numeric response. `IronCondorStrategy._pre_entry_margin_ok` consults it right after the FREEZE_QTY guard in BOTH `enter()` (legacy sequential) and `enter_hedge_first()` (LIVE-25 default) paths; refuses with `IC_REJECT reason=INSUFFICIENT_MARGIN instrument=... required=... available=... buffer=...x ...` structured log. Settings: `IC_MARGIN_CHECK_ENABLED=True` + `IC_MARGIN_BUFFER_MULT=1.2`. 15 regression tests in `tests/test_margin_check.py` pin the pure-function arithmetic, both order-manager flavours' happy / error / malformed paths, the structured IC_REJECT log, the query-exception → refuse behavior, the feature flag off-switch, and buffer-multiplier sensitivity.
- **Severity:** High
- **Severity reason:** The exact scenario rollback was built for (leg-3 rejected mid-entry) is the one easiest to prevent upfront with a `get_limits()` call.
- **Priority:** P0
- **Priority reason:** Direct cause of the LIVE-03 worst case. Prevention dominates recovery.
- **Evidence:**
  - `main.py:468-485` — entry decision path has no margin lookup.
  - `trading_system/core/iron_condor.py:187-273` — `enter()` has no margin parameter.
- **Impact:** Without the pre-check, mid-entry rejections are routine in live, amplifying LIVE-03's exposure surface.
- **Suggested approach:** Add `broker.get_limits()` to `MarketData` (or a new `AccountInfo` adapter). Before leg 1, compute required SPAN+Exposure for the 4-leg structure at our lot size; refuse entry if available margin < required × 1.2 (buffer for intraday shifts). Regression test: stub limits to 0.5× required; assert entry is refused and no orders are sent.

### LIVE-11 · Peak-margin and intraday margin phases unmodeled
- **Status:** Addressed 2026-04-24 — `LiveOrderManager.get_margin_shortfall()` returns rupees of `marginused - cash` (clamped at 0). `PaperOrderManager.get_margin_shortfall()` returns 0. `main.py`'s main loop polls it once per cycle in live mode and, on any positive shortfall, logs critical, fires a `intraday_margin_shortfall` LIVE-23 alert, and sets `risk.halted=True`. Fails open on query errors (transient API hiccups don't halt — LIVE-24 heartbeat catches silent death). 6 regression tests in `tests/test_intraday_margin.py` pin the solvent/shortfall/fail-open paths on both managers.
- **Severity:** Medium
- **Severity reason:** SEBI peak-margin rules snapshot margin at intervals through the day. A position that passed margin at entry can trigger a shortfall later if the index moves, especially near the 15:00 snapshot.
- **Priority:** P1
- **Priority reason:** Only matters once LIVE-10 is in place.
- **Evidence:** No references in the codebase to peak margin or margin-shortfall handling.
- **Impact:** Broker-side auto-squareoff or margin penalty without engine awareness. The engine continues to think the IC is intact.
- **Suggested approach:** Poll margin usage vs availability once per cycle in the monitor loop. On shortfall > 0, halt new entries; on shortfall > configurable threshold, proactively harvest or hedge. Regression test: drop available margin below used margin mid-session; assert entry halt fires.

### LIVE-12 · Cost stack excludes GST, exchange transaction charges, SEBI fees, stamp duty
- **Status:** Addressed 2026-04-24 — `compute_taxes_and_fees(symbol, side, price, qty)` in `trading_system/core/fees.py` returns the full six-component stack (brokerage, STT, exch_txn, SEBI, stamp, GST, total). Applied in `PaperOrderManager._build_fill()` on every entry and in `PaperPositionTracker.close_position()` on every exit — side-asymmetry (STT only on SELL, stamp only on BUY) is preserved. Rates pinned in `settings.FEES_NIFTY_OPT`, verified 2026-04-24 against Shoonya FAQ (brokerage ₹5 flat) + Zerodha's charges page (exch 0.03553%, SEBI ₹10/cr, stamp 0.003% BUY, GST 18% base=brok+exch+sebi) + Budget 2026 STT hike (0.15% effective 2026-04-01, up from 0.10%). Previous setting was 0.05% — three Budget-revisions stale. Paper-orders CSV widened to carry all six components so `ops/reconcile.py` can diff each line against the Shoonya contract note (already referenced via its 6-column `cost_cols` tuple). 16 new regression tests in `tests/test_fees.py` pin each component, the GST base exclusion (STT+stamp not GSTed), side-asymmetry, worked ₹68.31 SELL / ₹7-ish BUY examples, and a rates-block guard that fails loudly if `FEES_NIFTY_OPT` is edited without test updates.
- **Severity:** Medium
- **Severity reason:** Paper layer models only STT on the sell side + flat ₹5 brokerage per order. Live adds ~₹50–₹200 per round-trip 4-leg cycle depending on tier. The 1% harvest threshold is already near break-even at 10 lots; these missing costs can flip cycles net-negative.
- **Priority:** P1
- **Priority reason:** Directly corrupts the harvest-profitability assumption; must be modelled before trusting paper P&L in the go-live evaluator (LIVE-21).
- **Evidence:**
  - `trading_system/paper/paper_order_manager.py:100-106` — only `STT_FUTURES` / `STT_OPTIONS_SELL` computed.
  - `trading_system/config/settings.py:106-108` — constants.
- **Impact:** Every harvest cycle over-credits the engine by the missing cost stack. The go-live evaluator's `net_pnl_pos` and `ev_positive` checks are biased high.
- **Suggested approach:** Add `compute_taxes_and_fees(symbol, side, price, qty)` returning `{stt, exch_txn, sebi, stamp, gst_on_total}`. Apply in both entry and exit cost calcs. Source rates from a `settings.FEES_NIFTY_OPT` config block. Regression test: compute round-trip cost on a canonical 10-lot NIFTY IC at ₹18 credit; assert within ±5% of a manually-derived expected number from Shoonya's published charges.

### LIVE-13 · Freeze-quantity limit not enforced
- **Status:** Addressed (2026-04-23) — `FREEZE_QTY_NIFTY=1800` and `FREEZE_QTY_BANKNIFTY=900` added to `settings.py`; `IronCondorStrategy.enter` refuses entry with a structured `IC_REJECT reason=FREEZE_QTY_BREACH` log before any leg is placed. 2 regression tests in `tests/test_ic_strategy.py` (breach + boundary).
- **Severity:** High
- **Severity reason:** NSE enforces a per-order freeze quantity (e.g. NIFTY options currently 1800 qty ≈ 28 NIFTY lots at 65/lot). Today's 10 lots × 65 = 650 is within limit. But lot sizes and freeze quantities change; any scale-up or lot-size change silently breaches it, producing exchange-side rejections the engine does not classify distinctly.
- **Priority:** P0
- **Priority reason:** A freeze-qty reject mid-entry triggers rollback (LIVE-03). Prevention is cheap.
- **Evidence:** No references to freeze quantity anywhere in the codebase.
- **Impact:** A future settings change lifts `IC_LOT_SIZE` above the implicit freeze-qty threshold; live entries start failing on leg 3 unpredictably.
- **Suggested approach:** Add `FREEZE_QTY_NIFTY` / `FREEZE_QTY_BANKNIFTY` to `settings.py`. In `IronCondorStrategy.enter`, refuse entry if `qty > freeze_qty`. Regression test: set `IC_LOT_SIZE` such that qty exceeds configured freeze; assert entry refuses and logs a structured reason.

## Category 5 — Real-time gap

### LIVE-14 · 60-second polling cadence and tick-count confirmation lag harvest and stop triggers
- **Status:** Open
- **Severity:** Medium
- **Severity reason:** `SIGNAL_RECHECK_SEC=60` plus `LTP_CACHE_SEC=2` means the harvest check and combined hard-stop can lag the market by up to a minute. `IC_HARD_STOP_CONFIRM_TICKS=2` at that cadence is up to 2 minutes of breach before halt — designed to reject one-tick noise but the confirmation window is far too wide for live gamma days. Both knobs share one underlying fix.
- **Priority:** P1
- **Priority reason:** Moving to WS fills+quotes is substantial work.
- **Evidence:**
  - `trading_system/config/settings.py:30` — `SIGNAL_RECHECK_SEC = 60`.
  - `trading_system/config/settings.py:45` — `IC_HARD_STOP_CONFIRM_TICKS = 2`.
  - `trading_system/core/risk_manager.py:66-77` — breach-streak logic.
  - `main.py:500` — the sleep call in the main loop.
  - `data_collector.py` — background tick collection at 5s cycle but not wired into the strategy decision path.
- **Impact:** Missed harvests directly; stops fire 90–120s after they should. At 10 lots the deviation between intended 3× stop and realised loss is meaningful.
- **Suggested approach:** (a) Use Shoonya WS subscription for the four leg symbols plus spot, update an in-memory mark on tick, and tighten the monitoring-only path to 5s cadence while keeping entry cadence at 60s. (b) Express the hard-stop confirmation window in seconds (`IC_HARD_STOP_CONFIRM_SEC=6`) rather than tick counts, and derive the tick requirement from the monitoring cadence. At 5s cadence, 6s = 2 ticks still. Regression tests: (a) tick sequence crossing the harvest threshold mid-minute and reverting — assert exit fires against the crossing tick; (b) breach at monitoring cadence — assert time-from-first-breach-to-halt ≤ configured seconds + one cycle.

## Category 6 — Operational

### LIVE-17 · Shoonya rate-limit pressure under harvest churn
- **Status:** Deferred 2026-04-26 — out of go-live blocker scope. The rate-limit pressure scenario assumes both instruments harvesting concurrently at full lot size; the planned shakedown caps `IC_MAX_ENTRIES_PER_SESSION=1`, which keeps at most 1 IC open and ~4 legs to monitor, well under the existing 10/sec throttle headroom. Revisit before scaling past `IC_MAX_ENTRIES_PER_SESSION=1` or before enabling both instruments concurrently in live.
- **Severity:** Medium
- **Severity reason:** `api_helper.py` has a 10 calls/sec cap. Simultaneous harvest on both NIFTY and BANKNIFTY (4 exit + 4 entry legs each = 16 order calls in rapid succession) plus per-cycle LTP polling can saturate the lane.
- **Priority:** P2
- **Priority reason:** Existing throttle likely handles this, but deserves a concrete load test before live.
- **Evidence:**
  - `api_helper.py` — 10 calls/sec hard cap with priority lanes.
  - `trading_system/core/iron_condor.py::monitor` + `exit` — ~20 API calls within ~5 seconds during harvest.
- **Impact:** Under simultaneous harvest churn, low-priority LTP polling queues behind order placement. Quote staleness can cross a threshold the 2-second cache cannot hide.
- **Suggested approach:** Add a throttle-saturation log counter. Dry-run a back-to-back harvest scenario in paper with a live-API harness; record headroom against the cap. If headroom < 2 slots/sec, reorder harvest to close-all-then-enter-all rather than per-instrument ping-pong. Regression test: simulate 16 calls in 1 second against a throttle stub; assert all complete and headroom > 0.

### LIVE-18 · Market-anomaly states (circuit, pre-open, auction, muhurat) are unhandled
- **Status:** Addressed 2026-04-24 — `strategy_runner.is_tradable_now(symbol=None, now=None, broker_halt_flag=None) -> (bool, reason)` is the single authority consulted by `RegimeFilter.get_regime_gate` as its FIRST check. Refuses with named reason tags (`pre_open`, `before_open`, `after_close`, `weekend`, `holiday`, `muhurat_closed`, `broker_halt`) that surface in `IC_REJECT`-style log lines. Muhurat sessions (`settings.MUHURAT_SESSIONS`, currently empty — operator populates from NSE muhurat circular) become the SOLE tradable window on their date, overriding the usual weekend/holiday logic. Pre-open window `settings.PRE_OPEN_START_IST`/`PRE_OPEN_END_IST` (09:00–09:15) newly refused. `broker_halt_flag` is plumbing-only until the live broker surface lands. Regression: 19 tests in `tests/test_market_session.py` — every refusal path's reason tag, half-open muhurat boundary, malformed-MUHURAT_SESSIONS graceful skip, regime-gate integration (forwards the halt flag, refuses when clock says no).
- **Severity:** Medium
- **Severity reason:** The code flow assumes continuous regular-session trading 09:15–15:30. Pre-open session (09:00–09:15) can return quotes that look valid but cannot be acted on. Circuit halts freeze the book entirely. Muhurat session is a single short window on a different date.
- **Priority:** P1
- **Priority reason:** Rare but catastrophic if an entry fires during a halt or auction-only window.
- **Evidence:**
  - `strategy_runner.is_market_hours` / `is_market_closed_ist` — only checks the regular session window.
  - No references to circuit-halt state or auction awareness anywhere.
- **Impact:** Quotes look clean; orders get auto-rejected or queued until normal session resumes; the rejection cascade triggers LIVE-03 exposure window.
- **Suggested approach:** Add `is_tradable_now(symbol)` consulting session window, holiday calendar, and any broker-reported halt flag. Refuse entries outside tradable windows. Regression test: fake broker returning a halt flag for NIFTY; assert entry is refused.

### LIVE-19 · No kill-switch file
- **Status:** Addressed 2026-04-22 — `main._check_kill_switch()` runs at the top of every main-loop iteration (`main.py:166`). When `settings.HALT_FILE` (default `data/HALT`) exists, force-exits all active strategies, persists state, fires a `kill_switch_activated` LIVE-23 critical alert, removes the HALT file, and exits cleanly. The operator drops the file from anywhere — another terminal, ssh, even iCloud Drive — and the next cycle picks it up. Regression: `tests/test_safety_controls.py::TestKillSwitch` pins file-absent / file-present-flatten / file-present-exit / file-removed-after-handle.
- **Severity:** High
- **Severity reason:** Today's only halt paths are programmatic (combined stop, rollback escalation). No operator-initiated emergency stop works without access to the terminal running the process.
- **Priority:** P0
- **Priority reason:** Must exist before first real trade; trivial to add.
- **Evidence:** `main.py:398-500` — no check for a halt file.
- **Impact:** An operator who notices a problem cannot stop trading from a different machine or terminal without SIGINT (not clean mid-order-sequence).
- **Suggested approach:** Top of the main loop: `if os.path.exists('data/HALT'): force_exit_all; save_state; sys.exit(0)`. Regression test: drop the file and run one loop iteration; assert force-exit is called and process exits.

### LIVE-20 · No single-instance guard (PID file)
- **Status:** Addressed 2026-04-22, hardened 2026-04-26 — `main._acquire_pid_lock()` (`main.py:66`) writes our PID to `settings.PID_FILE` (default `data/regimetrader.pid`) at startup and refuses to start if the file holds a still-alive PID. Saturday's hardening: the liveness check is `os.kill(pid, 0)` *combined with* the freshness of `data/pnl_snapshot.json` — a stale PID from a macOS-sleep-killed process or an `os.kill`-resilient SIGSTOP'd process no longer blocks legitimate restarts. PID file is removed on clean shutdown. Regression: `tests/test_safety_controls.py::TestPidLock`.
- **Severity:** High
- **Severity reason:** A second accidental `./start.sh` would place duplicate orders against the same account. No guard prevents this.
- **Priority:** P0
- **Priority reason:** Easy to mis-click in live. Trivial to implement.
- **Evidence:**
  - `start.sh` — no PID-file logic.
  - `main.py:344-395` — no PID check at startup.
- **Impact:** Double-size position unintentionally. Margin blows out. At 10 lots × 2 instances, a 4-leg IC suddenly becomes 8-leg.
- **Suggested approach:** At startup, check `data/regimetrader.pid`. If present and PID is alive (`os.kill(pid, 0)` returns without error), refuse to start. Write our PID on start; delete on clean shutdown. Regression test: fork two processes; assert second exits with a structured "already running" error.

### LIVE-22 · No daily rupee loss cap
- **Status:** Addressed 2026-04-22, shakedown-tightened 2026-04-26 — `RiskManager.check_daily_loss_cap(pnl_engine)` (`risk_manager.py:101`) consulted once per main-loop cycle (`main.py:980`). Effective cap is `settings.DAILY_MAX_LOSS_SHAKEDOWN` (₹10k) when `SHAKEDOWN_MODE=True`, else `settings.DAILY_MAX_LOSS` (₹50k). On breach: halt entries, force-exit-all, fire `daily_loss_cap_breached` LIVE-23 critical alert with the actual cap that fired in the body. Cap reads `pnl_engine.daily_realised_pnl + pnl_engine.unrealised_pnl` so an open IC's mark-to-market mid-day breach trips it without waiting for harvest. Regression: `tests/test_safety_controls.py::TestDailyLossCap` (steady-state) + `TestShakedownDailyLossCap` (proving-period).
- **Severity:** High
- **Severity reason:** The only current loss limiter is the per-IC `IC_STOP_LOSS_MULT=3.0` multi-leg combined stop. There is no absolute rupee cap per day. A streak of losing ICs can exceed personal tolerance long before a combined 3× stop fires.
- **Priority:** P0
- **Priority reason:** Must be configurable and active on day 1 of live. Independent of any structural stop.
- **Evidence:**
  - `trading_system/core/risk_manager.py:23-81` — only `check_combined_stop_loss` exists.
  - `trading_system/config/settings.py` — no `DAILY_MAX_LOSS` constant.
- **Impact:** Open-ended daily loss with no explicit operator-set ceiling.
- **Suggested approach:** Add `DAILY_MAX_LOSS` to `settings.py` (operator chooses a number they can stomach at 10 lots × both instruments — likely ₹50k–₹100k). In the main loop, when `pnl_engine.daily_realised_pnl + pnl_engine.unrealised_pnl < -DAILY_MAX_LOSS`, halt entries and flatten. Regression test: construct a daily P&L state just below the cap; advance price to push unrealised over; assert halt-and-flatten is called exactly once.

### LIVE-23 · No operator alert channel
- **Status:** Addressed 2026-04-22 (code) + 2026-04-24 (smoke test + Unicode hardening). `AlertChannel` protocol with `NtfyAlertChannel` (daemon-thread POST worker, 60s per-event dedup, bounded queue) + `LogAlertChannel` / `NullAlertChannel` fallbacks. `build_channel` factory reads `settings.ALERTS_ENABLED` / `ALERTS_CHANNEL` + `cred.yml::ALERTS_NTFY_TOPIC_URL`. End-to-end smoke test `tools/smoke_test_alerts.py` validates (a) POST headers/body across severities, (b) async worker drain under bursts, (c) `flush()` drain for short-lived scripts, (d) within-window dedup. Verified against `https://ntfy.sh/regimetrader-arsh-a9f2k1q7x` — 10/10 notifications server-side on the post-fix run. Smoke test surfaced a real production bug: HTTP Title headers must encode as latin-1, so em-dash / ₹ / emoji raised UnicodeEncodeError inside urllib3 and the alert vanished silently. Hardened via `NtfyAlertChannel._latin1_safe`, regression pinned in `tests/test_alerts.py::test_unicode_title_does_not_silently_drop_alert`.
- **Severity:** Medium
- **Severity reason:** The system today halts on its own but has no way to tell the operator it halted. Stop-loss hits, rollback escalations (LIVE-03), auth failures (LIVE-16), unhandled exceptions — all land in the log file only.
- **Priority:** P1
- **Priority reason:** The LIVE-03 hedge-and-alert path depends on this existing. Must ship alongside LIVE-03 or immediately after.
- **Evidence:** No references to Telegram, ntfy, webhook, or email anywhere in the codebase.
- **Impact:** A halted system is operationally equivalent to a crashed one until the operator notices. Silent halts during market hours cost option value minute-by-minute.
- **Suggested approach:** Single integration — Telegram bot or `ntfy.sh` (free, no auth setup). Alert events: stop-loss hit, entry rejection cascade, auth failure, rollback escalation, unhandled main-loop exception, daily-loss cap hit. ~30 lines. Read Telegram/ntfy config from `cred.yml`. Regression test: trigger each alert event in a fake; assert the correct channel payload is emitted.

### LIVE-24 · No external heartbeat / silent-death detection
- **Status:** Addressed (2026-04-23) — `tools/heartbeat_check.py` + `deploy/launchd/com.regimetrader.heartbeat.plist`. Off-hours silent, fires `heartbeat_stale` / `heartbeat_missing` critical alerts during market hours if `data/pnl_snapshot.json` is older than `--stale-sec` (default 180s). 7 unit tests in `tests/test_heartbeat_check.py`. Operator install: copy the plist to `~/Library/LaunchAgents/`, substitute `REPLACE_WITH_HOME` with `$HOME`, then `launchctl load`.
- **Severity:** Medium
- **Severity reason:** If the process dies silently (OOM, macOS force-kill, sleep), open positions are unmonitored until the operator notices. There is no external watchdog.
- **Priority:** P1
- **Priority reason:** Low probability on a healthy laptop, but the blast radius at 10 lots is large.
- **Evidence:** No launchd/cron job or external monitor configured in the repo.
- **Impact:** A dead process between 09:15 and 15:10 with open ICs is the same as LIVE-23 silent halt but with no halt action taken.
- **Suggested approach:** `launchd` job on the same Mac that checks `mtime` of `data/pnl_snapshot.json` every 5 min during market hours (09:15–15:30 IST). If stale > 3 min, ping the LIVE-23 alert channel. Regression test: freeze `mtime` on the snapshot and invoke the check script; assert alert fires.

## Category 6.4 — Operational fine-tuning (paper-observed)

### LIVE-26 · Per-instrument harvest thresholds
- **Status:** Addressed 2026-04-30 — `IC_HARVEST_PCT_BY_INSTRUMENT = {"NIFTY": 0.02, "BANKNIFTY": 0.13}` replaces the single `IC_HARVEST_PCT = 0.01`. BANKNIFTY requires 13% due to its wider absolute credit range and higher lot-size costs. Flat-session PnL restore fixed alongside. Regression tests in `test_ic_strategy.py`.

### LIVE-27 · BANKNIFTY credit floor ₹30 → ₹25
- **Status:** Addressed 2026-04-30 — `IC_MIN_CREDIT_BY_INSTRUMENT["BANKNIFTY"]` lowered from ₹30 to ₹25. The ₹30 floor was blocking all afternoon re-entry cycles on wide-spread BANKNIFTY days where credit legitimately settles in the ₹25–₹30 range.

### LIVE-28 · VIX stability window 15 → 8 minutes
- **Status:** Addressed 2026-04-30 — `IC_VIX_STABLE_MINS = 8` (was 15, was 45 before LIVE-22). Opening-hour VIX swings resolve within 8 min; the 15-min window was blocking entries until ~10:45 on most days.

### LIVE-29 · S/R OTM cap to prevent illiquid strike selection
- **Status:** Addressed 2026-04-30 — `IC_SR_CAP_OTM_FROM_SPOT = {"NIFTY": 400, "BANKNIFTY": 1000}`. When a 20-day range forces short strikes more than 400/1000 pts from spot, strikes are clamped back and a WARNING is logged. Prevents credit collapse on wide-ranging days.

### LIVE-30 · NIFTY min-VIX gate at regime layer
- **Status:** Addressed 2026-04-30 — `IC_NIFTY_MIN_VIX = 14.0` check moved to `get_regime_gate(instrument=)` in `regime_filter.py`. On VIX < 14, NIFTY credit is structurally below the ₹18 fee break-even; refusing at the regime gate saves 500+ wasted quote-API calls per quiet day. BANKNIFTY is unaffected.

## Category 6.5 — Shakedown mode (proving-period controls)

Safety triad layered on top of the steady-state safety controls (LIVE-19/20/22),
applied during the first ~10 trading days of live operation while the LIVE-21
reconciliation gate builds a verifiable track record. Operator flips
`settings.SHAKEDOWN_MODE = True` before the first live session and clears it
once reconciliation passes. Has zero effect in paper mode.

### SHAKEDOWN-01 · LIVE_ACK operator handshake
- **Status:** Addressed 2026-04-26 — `main._require_live_ack()` runs immediately after `_acquire_pid_lock()`. In live mode (`PAPER_TRADE_MODE=False`), startup refuses to proceed unless `settings.LIVE_ACK_FILE` (default `data/LIVE_ACK`) exists; paper mode bypasses the gate entirely. File contents are not parsed — presence alone is the signal. Blocks the failure mode where `PAPER_TRADE_MODE` is flipped to False without a deliberate operator decision (config drift, bad rebase, accidental edit). 3 regression tests in `tests/test_safety_controls.py::TestLiveAck` pin paper-bypass / live-without-ack-exits / live-with-ack-proceeds.
- **Severity:** High — protects against silently going live with stale config.
- **Priority:** P0-shakedown
- **Operator runbook:** `touch data/LIVE_ACK` after confirming OAuth freshness, calibration acceptance, and SHAKEDOWN_MODE=True. File persists across restarts.

### SHAKEDOWN-02 · Tighter daily-loss cap during proving period
- **Status:** Addressed 2026-04-26 — `RiskManager.check_daily_loss_cap()` now reads `settings.DAILY_MAX_LOSS_SHAKEDOWN` (default ₹10k) when `settings.SHAKEDOWN_MODE=True`, otherwise falls back to `DAILY_MAX_LOSS` (₹50k). Sized for proving-period blast radius (1 IC × 10 lots, max loss ≈ ₹32k on a 50pt-wing NIFTY). Critical alert body references the actual cap that fired (not the steady-state default) so the operator knows which threshold tripped. 4 regression tests in `tests/test_safety_controls.py::TestShakedownDailyLossCap`.
- **Severity:** High
- **Priority:** P0-shakedown

### SHAKEDOWN-03 · Per-(instrument, IST date) entry cap
- **Status:** Addressed 2026-04-26 — `IronCondorStrategy._check_session_entry_cap()` returns False once `_entries_today_count` reaches `settings.IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN` (default 1). Counter is per-strategy-instance, resets on IST date rollover. Active only when `SHAKEDOWN_MODE=True`; in paper, always returns True so paper data collection is never capped. Hooked at the top of both `enter()` (sequential path) and `enter_hedge_first()` (LIVE-25 default), before strikes calc. Successful entries call `_record_session_entry()` from the position-set success path; unsuccessful attempts do not consume the cap. Once hit, refusal is logged as `IC_REJECT reason=SHAKEDOWN_ENTRY_CAP …` — Axiom 3 non-participation during the proving period. 7 regression tests in `tests/test_safety_controls.py::TestShakedownEntryCap` including two wiring pins that fail if the cap-check is removed from either `enter()` or `enter_hedge_first()`.
- **Severity:** Medium
- **Priority:** P1-shakedown

### Pre-flip blockers (must resolve BEFORE setting SHAKEDOWN_MODE=True)

The shakedown triad is committable as-is because `SHAKEDOWN_MODE` defaults to False — none of the controls fire in paper or in steady-state live. One blocker resolved 2026-04-26; one remains as an operator-decision item.

- **SHAKEDOWN-03a · Counter persistence across crash-restart.** Addressed 2026-04-26. `IronCondorStrategy.save_state` / `restore_state` now wrap `{"position": ..., "entries_today_count": int, "entries_today_date": ISO_DATE}` and the persistence layer saves a strategy entry whenever EITHER a position OR a non-zero shakedown counter exists (so post-harvest flat state still persists the consumed budget). Restore re-keys on the IST date — same-day crash-restart preserves the count, next-day restart resets cleanly. Fail-closed posture for shakedown: operator-intent (across-day) resets are correct; crash-class (same-day) resets are blocked. Regression: 5 tests in `tests/test_safety_controls.py::TestShakedownCounterPersistence` covering save-when-flat omission for paper, save-includes-counter when shakedown-exhausted-and-flat, same-IST-date round-trip, IST-date-rollover reset, and combined position+counter round-trip.

- **SHAKEDOWN-03b · Harvest semantics — loose, ratified 2026-04-26.** The cap counts only fresh entries; same-day `PROFIT_HARVEST` and `ADJUSTMENT_REQUIRED` re-entries bypass the cap so the harvest-and-re-enter loop runs at full cadence during the proving period. `FORCE_EXIT` and any future stop-loss reason are intentionally excluded — once a session is halted, no further entries that day. `DAILY_MAX_LOSS_SHAKEDOWN`=₹10k bounds the downside of unlimited harvest churn.

  Implementation: `IronCondorStrategy._is_re_entry()` returns True when `_last_exit_reason` is in `_RE_ENTRY_EXIT_REASONS = ("PROFIT_HARVEST", "ADJUSTMENT_REQUIRED")` AND `_last_exit_date == today`. `exit()` stamps both fields just before returning. `_check_session_entry_cap` short-circuits to True on a re-entry; `_record_session_entry` skips the bump on a re-entry. The sentinel is persisted alongside the counter (gated on same-IST-date) so a crash between `exit()` and the next `enter()` doesn't lose the harvest signal. Regression: 8 tests in `tests/test_safety_controls.py::TestShakedownLooseHarvestSemantics` covering the exit-stamping wiring, harvest/adjustment bypass, force-exit non-bypass, 100-iteration unbounded harvest cycle, IST-date isolation, and the crash-between-exit-and-reentry round-trip.

## Category 7 — Meta

### LIVE-21 · Go-live evaluator grades paper-optimistic numbers
- **Status:** Addressed 2026-04-23 — `live_reconciled_trades` check added; evaluator caps verdict at KEEP PAPER TRADING unless `GL_MIN_RECONCILED_DAYS` (=10) days of LIVE-08 reports exist with every matched leg within `GL_RECONCILED_PRICE_DRIFT_PCT` (=2%) and zero unmatched legs. Stricter than the original aggregate-PnL spec: per-leg drift is checked against a pinned threshold re-derived in the evaluator (doesn't trust the writer's `flagged_count`, which depends on the operator's `--flag-pct`). Loader lives at `trading_system.ops.reconcile.load_reconciliation_reports()`. Regressions pinned in `tests/test_go_live_evaluator.py` (11 new tests) and `tests/test_reconcile.py` (3 loader tests).
- **Severity:** High
- **Severity reason:** `GoLiveEvaluator` enforces win rate, win/loss ratio, drawdown, and PnL thresholds — but computes them entirely against paper fills. The fill model (LIVE-04, LIVE-05, LIVE-06) and cost model (LIVE-12) are systematically biased optimistic. A "GO LIVE" verdict today does not mean live would pass the same thresholds.
- **Priority:** P0
- **Priority reason:** The meta-trap: the very mechanism that is supposed to gate going live cannot detect its own fill-model bias.
- **Evidence:**
  - `trading_system/paper/go_live_evaluator.py:25-79` — all checks read from `summary` / `trades_df` / `orders_df`, all paper-side artefacts.
  - `trading_system/paper/go_live_evaluator.py:111-120` — `_plausible_win_rate` is the only guard against the fill-model lie (90% cap, 30-trade minimum heuristic).
- **Impact:** A strategy profitable on paper but break-even or negative under live slippage+fees will pass the evaluator. Paper won't show the problem because paper constructs the fills.
- **Suggested approach:** Add a `LIVE_RECONCILED_TRADES` check to `GoLiveEvaluator` — require N days of live-reconciled trades (from LIVE-08) where `|engine_pnl − broker_pnl| / |engine_pnl| < 2%`. If absent, verdict caps at `KEEP PAPER TRADING` regardless of paper numbers. Until LIVE-08 ships, the evaluator's verdict carries a prominent disclaimer. Regression test: pass a paper summary that would otherwise verdict `GO LIVE`, but with no reconciliation artefact; assert verdict gated to `KEEP PAPER TRADING`.

---

# Suggested fix order

Prioritised by exposure prevention first, then decision-quality:

1. ~~**LIVE-19, LIVE-20, LIVE-22** — kill switch, single-instance, daily loss cap.~~ Done 2026-04-22.
2. ~~**LIVE-23, LIVE-24** — alert channel + heartbeat.~~ Done 2026-04-23.
3. **LIVE-01** — async order plumbing. Paper-side stub landed 2026-04-22; live-side terminal-status awaiter still pending and emerges with LIVE-25.
4. **LIVE-07** — startup reconciliation. Prerequisite for trusting any live run after a crash.
5. **LIVE-10, LIVE-13** — margin + freeze-qty pre-checks. Eliminate the most common causes of LIVE-03.
6. **LIVE-06** — bid/ask visibility. Required to set LIVE-25's short-leg limit prices sensibly.
7. **LIVE-25** — hedge-first entry sequencing. Converts the naked-short exposure scenario into a bounded-premium scenario by construction. Narrows LIVE-03.
8. **LIVE-03** — rollback hedge path. Now scoped to the one-wing-fails edge case, not the unbounded-naked-short scenario.
9. **LIVE-02** — post-fill credit re-check. Becomes LIVE-25's Phase 5a gate.
10. ~~**LIVE-12**~~, **LIVE-04, LIVE-08** — cost stack (done 2026-04-24) + slippage calibration + per-trade reconciliation. Together these restore trust in paper numbers.
11. ~~**LIVE-21** — gate evaluator on reconciliation. The key deliverable that allows a credible GO LIVE verdict.~~ Done 2026-04-23.
12. **LIVE-05** — partials. Finish category 2 before proving period.
13. **LIVE-14** — WS + latency-bound stop. Substantial work; parallelisable with proving period.
14. **LIVE-11, LIVE-18, BUG-07** — peak margin, market anomalies, mid-session auth recovery. Robustness layer.
15. ~~**LIVE-09, LIVE-17**~~ — deferred 2026-04-26 (see "Explicitly deferred" below).

---

# Proving period (adapted for 10-lot minimum)

Enter this phase only after step 11 above (LIVE-21 gating on real reconciliation).

- **One instrument, 10 lots, two weeks.** Since we can't go smaller, reduce the other dimension: run NIFTY only (or BANKNIFTY only) for ~10 trading days at `IC_LOT_SIZE=10`. Comment out the other `IronCondorStrategy` instantiation in `main.py`. Half the exposure, same per-trade economics.
- **Nightly reconciliation** runs automatically (LIVE-08). Divergence > 2% on any single trade: find the bug before the next session.
- **Enable both instruments only after reconciliation is clean for a full week.** Don't scale until the single-instrument run is boring.

# Laptop-specific operational gotchas

These are behaviour rules for the operator, not code items.

- **Keep the Mac awake.** `caffeinate -di` in a dedicated terminal while the process runs, or use Amphetamine. A sleeping laptop at 14:14 IST with open condors on both indices = catastrophic.
- **Power + network.** Plug in. Know your phone hotspot password. Shoonya has maintenance windows — check their status page before session start.
- **Credential hygiene.** `cred.yml` is git-ignored — keep it that way. Don't paste tokens into chat, screenshots, or commits. macOS Keychain migration later; not a day-one blocker.

# Shortest realistic path

- LIVE-19/20/22/23/24 (safety + alerts): ~1 evening
- LIVE-01/07/10/13 (async plumbing + startup reconciliation + margin/freeze pre-checks): ~4–5 days
- LIVE-06/25 (bid/ask visibility + hedge-first entry sequencing): ~1 week
- LIVE-03/02 (narrowed rollback hedge + post-fill credit gate): ~2–3 days
- LIVE-12/04/08/21 (economics + reconciliation gate): ~3–5 days
- Proving period: 2 weeks of real-money observation, single instrument
- Total: **~4 weeks** from today to both-instrument live operation at 10 lots.

# Explicitly deferred (don't need on day one)

- Multi-environment config (dev/staging/prod) — one laptop, one config.
- Process supervisor like systemd — manual restart is fine for solo.
- Secret vaults — `cred.yml` is adequate for a single operator.
- Chaos/fault-injection testing — proving period is your fault injection.
- SEBI algo-trading registration — check with broker if the request rate triggers their threshold; likely fine at this frequency.
- **LIVE-09 (tag collisions)** — deferred 2026-04-26. Hygiene only; reconciliation does not depend on the tag. Revisit post-shakedown if hand-reading `paper_orders.csv` across restarts becomes painful.
- **LIVE-17 (rate-limit pressure)** — deferred 2026-04-26. Pressure scenario requires concurrent dual-instrument harvest at full size; shakedown's `IC_MAX_ENTRIES_PER_SESSION=1` cap keeps load nowhere near the 10/sec throttle. Revisit before lifting that cap or enabling both instruments concurrently in live.

# Open questions to decide before coding

- What is your `DAILY_MAX_LOSS` tolerance in absolute rupees? (LIVE-22)
- Start with NIFTY only or BANKNIFTY only for the proving period?
- Shoonya's actual brokerage tier for your account? (affects LIVE-12 calibration and LIVE-08 reconciliation tolerance)
- Telegram bot or ntfy for alerts? (LIVE-23) — **Decided 2026-04-22: ntfy.**
- `IC_SHORT_LIMIT_TIMEOUT_SEC` / `IC_SHORT_LIMIT_OFFSET_TICKS` / Phase 3 fallback (LIVE-25) — **Decided 2026-04-23: 600s / 0 ticks / refuse.** See `settings.py::IC_SHORT_LIMIT_*` and `IC_PHASE3_FALLBACK`.
