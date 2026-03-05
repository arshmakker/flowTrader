# Broker API Call Audit

This document estimates how many calls we make to the broker (Shoonya/Noren) so you can avoid throttling or blocks.

## get_positions() response fields (Shoonya API)

Verified against [ShoonyaApi-py](https://github.com/Shoonya-Dev/ShoonyaApi-py). We use:

| Field        | Description              | Our usage                          |
|-------------|--------------------------|------------------------------------|
| `exch`      | Exchange segment         | Filter NFO; `exch` or `exchange`   |
| `tsym`      | Trading symbol           | Match legs; `tsym` or `tradingsymbol` |
| `netqty`    | Net position quantity    | Leg size / open qty fallback       |
| `netavgprc` | Net position average price | Entry/leg price; primary key in `_get_avg_price_from_broker_row` |
| `openbuyqty` / `opensellqty` | Open buy/sell qty  | Open qty when `openqty` absent in `_effective_open_qty` |
| `urmtom`    | Unrealized MTOM          | MTM for exit, final_pnl, breakdown |
| `rpnl`      | Realized PNL             | Broker realized in MTM breakdown   |

## Summary (approximate)

| Source | Interval | Calls per run | Est. calls/min |
|--------|----------|----------------|----------------|
| **Data collector** | 1 s | N (symbols) get_quotes | **N × 60** (dominant if N large) |
| Position + imbalance check | 60 s | 1 get_positions + (if open positions) 1 get_quotes + P × M get_quotes | ~1–3 + P×M per min |
| IV calculation | 120 s | 1 get_quotes + ~20–40 get_quotes (chain count=10) | ~0.5 × (1+40) ≈ 20/min |
| Strategy check (Convex) | 300 s | 1 + chain(30) + 1 VIX + candles(0–2) + convex chains + snapshot | ~0.2 × 200–500 ≈ 40–100/min |
| Order placement (when trading) | On event | place_order × 2 + single_order_history every 2 s until fill | Burst: 2 + up to ~90 polls |

- **N** = number of symbols in data collection (cash + index derivatives; can be 100–500+).
- **P** = number of open positions; **M** = option chain size per position (e.g. 50 → ~100–200 get_quotes per position).

**Total in steady state (no new trades):**  
Roughly **N × 60 + 1 + 20 + 50 ≈ N × 60 + 70** get_quotes/get_positions per minute.  
If N = 200, that’s **12,000+** quote calls per minute; the data collector is the main driver.

---

## 1. Startup (once)

| Call | Count |
|------|--------|
| `api.get_positions()` (sync_from_broker) | 1 |

---

## 2. Data collector (background thread)

- **Interval:** Every **1 second** (`time.sleep(1)` in `data_collector.py`).
- **Per cycle:** One `api.get_quotes(exchange, token)` per symbol in `get_data_collection_symbols()` (NSE cash + index derivatives: NIFTY/BANKNIFTY/FINNIFTY futures + ATM options).
- **Symbol count:** From `symbol_manager.get_data_collection_symbols()` (cash + `get_all_index_derivatives()`). Can be **100–500+** depending on config.
- **Calls per minute:** **N × 60** (e.g. 200 symbols → 12,000 get_quotes/min).

**Risk:** Highest volume; most likely to hit rate limits if N is large.

---

## 3. Position check (main loop, every 60 s)

- **1** `api.get_positions()` per cycle (for sync only; imbalance check removed):
  - `position_tracker.sync_from_broker(api, positions_raw=...)` (sync tracker OPEN list from broker).
- If there are open positions:
  - `get_nifty_spot_price()` → **1** `api.get_quotes(NSE, NIFTY)`.
  - Per position: `get_option_chain_data(..., count=50)` → one `get_quotes` per option in strike range (~100–200 calls per chain).
- **Calls per 60 s:** 1 get_positions + 1 get_quotes + (num_positions × chain_size). Example: 2 positions → 1 + 1 + 2×150 ≈ **302** every 60 s.

---

## 4. IV calculation (main loop, every 120 s)

- `get_nifty_spot_price()` → **1** get_quotes.
- `get_option_chain_data(..., count=10)` → **~20–40** get_quotes (10 strikes each side × 2 for CE/PE).
- **Calls per 120 s:** ~41. **Per minute:** ~20.

---

## 5. Strategy check (main loop, every 300 s)

- `get_nifty_spot_price()` → **1** get_quotes.
- `get_option_chain_data(..., count=30)` → **~60–120** get_quotes.
- `build_market_state_from_chain()` → India VIX → **1** get_quotes(NSE, VIX).
- `get_recent_candles()` → `get_historical_price_data()`: usually **0** (uses stored data); fallback **1–2** (get_time_price_series or get_daily_price_series).
- Convex strategy: may fetch option chain for multiple expiries (e.g. 2–3 × ~60–120) → **~120–360** get_quotes.
- If proposal accepted: snapshot option chain `get_option_chain_data(..., count=50)` → **~100–200** get_quotes.
- **Calls per 300 s:** ~250–700 depending on expiries and snapshot. **Per minute:** ~50–140.

---

## 6. Order placement (on trade entry/exit)

- **Entry:** 2 × `api.place_order()` (long then short). Then `wait_for_order_fill()` polls `api.single_order_history(orderno)` every **2 s** until fill or timeout (long: 120 s → up to 60 polls; short: 60 s → up to 30 polls). Total: **2 + up to 90** calls per Convex entry.
- **Exit:** 2 × place_order (MKT) + same polling pattern.
- **Cancel (long not filled):** 1 × `api.cancel_order()`.

---

## Recommendations to avoid throttling

1. **Data collector**
   - Increase cycle interval (e.g. `time.sleep(5)` or `time.sleep(10)`) in `data_collector.py` to reduce N × 60/min.
   - Reduce number of symbols in `get_data_collection_symbols()` (e.g. only index derivatives, or a subset of cash) so N is smaller.

2. **Position check**
   - Keep 60 s; 1 get_positions + optional chains is modest unless you have many positions.
   - Consider increasing `POSITION_CHECK_INTERVAL` to 120 s if the broker is strict.

3. **Strategy check**
   - Keep 300 s; burst is acceptable.
   - Optionally increase `STRATEGY_CHECK_INTERVAL` to 600 s if needed.

4. **Option chain size**
   - Use smaller `count` where possible (e.g. IV already uses 10; position check could use 20–30 instead of 50) to reduce get_quotes per run.

5. **Single source of truth**
   - If you have a documented broker limit (e.g. “X requests per second”), set:
     - data collector interval and N so that N / interval_sec ≤ X, and
     - main-loop intervals so that (position + IV + strategy) calls per second stay below X.

---

## Where the code lives

| Component | File | Symbol / constant |
|-----------|------|--------------------|
| Data collector interval | `data_collector.py` | `time.sleep(1)` in `_collect_data` |
| Data collection symbols | `symbol_manager.py` | `get_data_collection_symbols()` |
| Position check interval | `main.py` | `POSITION_CHECK_INTERVAL = 60` |
| Imbalance check | `main.py` | same block as position check |
| IV interval | `main.py` | `IV_CALCULATION_INTERVAL = 120` |
| Strategy check interval | `main.py` | `STRATEGY_CHECK_INTERVAL = 300` |
| Option chain count | `strategy_runner.py` | `get_option_chain_data(..., count=...)` (10, 20, 30, 50) |
| Order fill polling | `strategies/convex/order_builder.py` | `ORDER_FILL_POLL_INTERVAL_SECONDS = 2` |
