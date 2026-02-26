# Live Convex Trading – Gaps and Fixes

Track and fix each gap before / during live trading.

| # | Gap | Priority | Status |
|---|-----|----------|--------|
| 1 | **Partial fills → orphan positions**: Long 1 fills, long 2 times out → broker has open leg, tracker has nothing. No cancel/reconcile. Process crash mid-sequence same. | High | **Done**: On partial completion we close filled legs via offsetting MKT orders and append a review entry to `partial_fill_reviews.json` for every such event. |
| 2 | **No cap on Convex positions**: Can add new backspread every 5 min; no max concurrent Convex positions. | High | TODO |
| 3 | **Max loss % doc vs code**: Doc says "1% of capital", code uses 10%. Clarify and align. | Medium | TODO |
| 4 | **Entry LMT at mid**: No slippage buffer; fast markets → no fill or partial fill. | Medium | TODO |
| 5 | **No daily loss limit**: Live path has no "stop new trades if daily P&L < -X". | High | TODO |
| 6 | **Profit target disabled**: `check_profit_target()` always False; profit-target exit path is dead code. | Low | TODO |
| 7 | **Market closed close**: On "market closed" we only update tracker, no broker exit; MIS usually auto-squares but mismatch possible. | Medium | TODO |
| 8 | **Regime lag**: 3-confirmation + 5-min check → can hold Convex too long after regime flip. | Low | TODO |
| 9 | **Single expiry**: Convex uses first expiry only; no fallback if illiquid. | Low | TODO |
| 10 | **Capital / sizing**: Capital is fixed (e.g. CAPITAL_8L); no check vs actual broker/risk. | Low | TODO |

---

## IOC orders (Shoonya + NSE)

Shoonya API and NSE support **IOC** (Immediate-or-Cancel) via `place_order(..., retention='IOC')`. See [ShoonyaApi-py](https://github.com/Shoonya-Dev/ShoonyaApi-py): `ret` (retention) can be **DAY / EOS / IOC**. Convex **entry** orders now use `ENTRY_RETENTION = "IOC"` in `strategies/convex/order_builder.py`: we get fast fill or cancel, no lingering pending orders, and quicker feedback when a leg doesn’t fill (helps with gap #1: react sooner to partial completion).

---

## Partial-fill cleanup and review (gap #1)

When entry is only partially complete (e.g. long filled, short cancelled), the code now:

1. **Closes filled legs** – Places offsetting MKT orders for each filled leg so the broker is flat (no orphan).
2. **Saves a review entry** – Appends one record to **`partial_fill_reviews.json`** (project root) with:
   - `timestamp`, `reason`, `message`
   - `filled_legs`: order_id, tradingsymbol, quantity, side
   - `cleanup_results`: per-leg cleanup_order_id, cleanup_ok, error (if any)
   - `proposal_info`: proposal_id, expiry, lots

After every such cleanup you can open `partial_fill_reviews.json` to review what happened and whether each close succeeded. File is capped at 500 entries (oldest dropped). It is listed in `.gitignore`.

---

## Suggested order to address

1. **#3** – Max loss doc vs code (quick: fix doc or constant).
2. **#2** – Max Convex positions (e.g. skip new Convex entry if already N open).
3. **#5** – Daily loss limit (e.g. no new Convex if daily P&L < -X).
4. **#1** – Partial fills: define behaviour (cancel filled leg, or retry shorts, or reconcile on startup) and implement.
5. **#4** – Entry slippage (e.g. LMT at mid + buffer or use MKT for one leg).
6. **#7** – Market-closed: document that MIS auto-squares and optionally add a “reconcile on next run” note.
7. **#6** – Profit target: either implement a real target or remove dead code.
8. **#8, #9, #10** – Tune later (regime lag, expiry choice, capital/sizing).

---

*Created: 2026-02-26*
