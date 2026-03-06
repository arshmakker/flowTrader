# Design goals

## North star

**All trades should be profitable and exit with positive TSL (trailing stop loss).**

- Entry, exit logic, TSL parameters, profit targets, and risk caps are tuned and validated with this in mind.
- We aim to lock in profit via trailing stop and avoid letting winners reverse into losers.
- When evaluating changes (e.g. exit conditions, activation thresholds, trail %), prefer options that increase the share of trades that exit with positive TSL.

See also: [CONVEX_FLOW_ANALYSIS.md](CONVEX_FLOW_ANALYSIS.md) (Design goal section).
