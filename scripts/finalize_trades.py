#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ['performance_by_regime.json', 'active_positions.json']


def parse_iso(t):
    if not t:
        return datetime.min
    try:
        return datetime.fromisoformat(t)
    except Exception:
        try:
            return datetime.fromisoformat(t.split('+')[0])
        except Exception:
            return datetime.min


def finalize_file(fname: Path):
    data = json.loads(fname.read_text())
    groups = {}
    for rec in data:
        tid = rec.get('trade_id') or f'__noid__{id(rec)}'
        cur_time = parse_iso(rec.get('exit_time') or rec.get('entry_time'))
        if tid not in groups or cur_time >= groups[tid][0]:
            groups[tid] = (cur_time, rec)
    finals = [r for (_, r) in groups.values()]

    # enforce max lots cap and mark final (use central config)
    import sys, os
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    try:
        from strategies.size_config import MAX_LOTS
    except Exception:
        MAX_LOTS = 20
    for rec in finals:
        # preserve original lots if present
        try:
            orig_lots = int(rec.get('lots')) if rec.get('lots') is not None else None
        except Exception:
            orig_lots = None
        if orig_lots is not None:
            rec['_original_lots'] = orig_lots
            rec['lots'] = min(orig_lots, MAX_LOTS)
        else:
            # ensure lots is present and within cap
            rec['lots'] = min(int(rec.get('lots') or 1), MAX_LOTS)
        rec['is_final'] = True
        rec['record_version'] = 1

    out_path = fname.with_suffix('.final.json')
    out_path.write_text(json.dumps(finals, indent=2))
    print(f'Wrote {out_path} ({len(finals)} records)')

    # archive original if not already archived
    raw = fname.with_suffix('.raw.json')
    if not raw.exists():
        fname.rename(raw)
        print(f'Archived original to {raw}')

    # move final to original
    out_path.rename(fname)
    print(f'Replaced {fname} with finalized dataset')


def main():
    for f in FILES:
        p = ROOT / f
        if p.exists():
            finalize_file(p)
        else:
            print('Missing', p)


if __name__ == '__main__':
    main()
