#!/usr/bin/env python3
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ['performance_by_regime.json','active_positions.json']

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
    finals = [r for (_,r) in groups.values()]
    for rec in finals:
        rec['is_final'] = True
        rec['record_version'] = 1
    out_path = fname.with_suffix('.final.json')
    out_path.write_text(json.dumps(finals, indent=2))
    print(f'Wrote {out_path} ({len(finals)} records)')
    # archive original\n+    raw = fname.with_suffix('.raw.json')\n+    if not raw.exists():\n+        fname.rename(raw)\n+        print(f'Archived original to {raw}')\n+    # move final to original\n+    out_path.rename(fname)\n+    print(f'Replaced {fname} with finalized dataset')\n+
def main():\n+    for f in FILES:\n+        p = ROOT / f\n+        if p.exists():\n+            finalize_file(p)\n+        else:\n+            print('Missing', p)\n+\n+if __name__ == '__main__':\n+    main()\n+
