# Duplication cleanup and archive log

Date: 2026-02-24T00:00:00Z

Summary:
- Archived original files:
  - `performance_by_regime.json` -> `performance_by_regime.raw.json`
  - `active_positions.json` -> `active_positions.raw.json`

- Replaced originals with authoritative deduped files keeping the latest record per trade_id (by max exit_time).
- Added metadata to kept records:
  - `is_final`: true
  - `record_version`: 1

Counts:
- `performance_by_regime.json` now contains 182 records (previously 306).
- `active_positions.json` now contains 142 records (previously 266).

Notes:
- Originals were preserved under `.raw.json` for audit; if further historical retention is required move them to safe storage.
- The selection rule for authoritative record: prefer record with largest `exit_time`; fallback to `entry_time` when `exit_time` missing.
- If you prefer a different rule (e.g. prefer latest write timestamp or merge fields), tell me and I will rerun.

