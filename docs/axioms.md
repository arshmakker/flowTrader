# PCR Trader Axioms

Non-negotiable invariants.

## 1. PCR credit spread only
The system trades no structure other than a two-leg PCR-driven vertical credit spread
on NIFTY weekly options. No Iron Condor, no naked options, no futures.

## 2. Single open position per instrument
One spread is held at a time. Entry only on Monday or Tuesday (09:20–10:00 IST) when
PCR is outside the neutral band (< 0.7 or > 1.3). The spread is held until:
- Expiry-day close at 14:45 IST, or
- MTM loss exceeds 2× entry credit (in-session stop), or
- 15:10 EOD force-exit.

Non-participation is acceptable when PCR is neutral, credit is below the minimum, or
the daily loss cap has been hit.

## 3. Safety overrides continuity
Uncertain exposure, order outcome, risk state, or market data halts new entries until
state is trustworthy again.

## 4. Atomic two-leg confirmation
A spread exists only after both legs are confirmed filled. Short-leg fill without long-leg
confirmation triggers immediate rollback; rollback failure triggers Axiom 3.

## 5. Single accounting path
All position-state and realised-P&L changes flow through a single sanctioned interface.
No direct mutation elsewhere.
