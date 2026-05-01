# skills.md — Iron Condor Strategist Skills

skills:
  - name: select_expiry_contract
    description: Selects the optimal weekly expiry based on the 3 DTE Rolling Rule to manage gamma risk.
    input: Current date, instrument (Nifty/BankNifty), weekly options calendar.
    output: Expiry date (Current Week if DTE ≥ 3, otherwise Next Week).
    error_handling: >
      Defaults to Next Week if the current week's liquidity is below threshold
      or if DTE calculation is ambiguous.

  - name: calculate_adaptive_strikes
    description: Determines the 4 Iron Condor strikes using VIX-scaled OTM distances and 20-day S/R buffers.
    input: India VIX, 20-day High/Low, Spot Price, Instrument.
    output: Dict with 4 strikes (SC, LC, SP, LP) and spread width (50/100/150).
    error_handling: >
      If VIX or S/R data is unavailable, defaults to the most conservative
      tier (>20 VIX) with widest OTM placement. Ensures a minimum 50-point
      clearance from 20-day High/Low.

  - name: evaluate_entry_gates
    description: Validates all pre-entry conditions including Regime, Classification, VIX stability, and Credit Rule.
    input: VIX stability (8 min window), day_type (RANGING), instrument, net_credit, spread_width.
    output: Boolean (True if all gates GREEN, False otherwise) + reason string.
    error_handling: >
      Blocks entry if day_type is TRENDING, VIX ≥ 30, VIX unstable (> 1.5-pt
      range over last 8 min), or NIFTY VIX < 14. Min credit floor: NIFTY ₹18/lot,
      BANKNIFTY ₹25/lot. All gates enforced before any leg is submitted.

  - name: execute_harvest_cycle
    description: Monitors for the per-instrument profit harvest trigger and manages the immediate re-entry loop.
    input: Current unrealized P&L, Max possible profit of the spread, instrument.
    output: Action (HARVEST/HOLD) and trigger for fresh entry.
    error_handling: >
      Harvest threshold is instrument-specific: NIFTY 2% of max profit,
      BANKNIFTY 13% of max profit. If P&L data is stale (> 30s), skips harvest
      cycle to prevent execution errors. Logs every harvest and re-entry with
      live VIX/SR context.

  - name: manage_breach_adjustments
    description: Executes rolls for tested and safe sides when a short strike is breached.
    input: Breach status (True/False), Total position P&L, Current strikes.
    output: Adjustment orders (Roll tested side OTM, Roll safe side closer).
    error_handling: >
      Adjustment is DISABLED if the overall position is at a net loss.
      Ensures the adjustment remains as cost-neutral as possible.

  - name: enforce_breach_protocol
    description: Monitors the 3x combined stop-loss and handles single-sided recovery logic.
    input: Combined P&L (Nifty + BankNifty), current_time, VIX trend.
    output: Action (HARD_CLOSE_ALL / RECOVERY_ENTRY / HOLD).
    error_handling: >
      If 3x loss is hit, all positions are exited immediately. Recovery
      entry is ONLY permitted if time < 1:00 PM and VIX is stable/falling.
