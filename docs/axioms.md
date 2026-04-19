# RegimeTrader Axioms

Non-negotiable invariants.

## 1. Iron condor only
The system trades no structure other than a four-leg iron condor.

## 2. Maintain valid exposure
During trading hours, the system holds a valid iron condor in each enabled instrument. Each IC exits when unrealised P&L reaches 1% of its calculated max profit, after which re-entry is attempted on the next cycle. Positions may carry across weekday trading-day boundaries but must be flat before any weekend or holiday gap and by the IC's expiry-day close. Non-participation is acceptable only when Axiom 3 is active or required quotes are invalid.

## 3. Safety overrides continuity
Uncertain exposure, order outcome, risk state, or market data halts new entries until state is trustworthy again.

## 4. Atomic multi-leg confirmation
An iron condor exists only after all four legs are confirmed filled. Partial fill triggers rollback; rollback failure triggers Axiom 3.

## 5. Single accounting path
All position-state and realised-P&L changes flow through a single sanctioned interface. No direct mutation elsewhere.
