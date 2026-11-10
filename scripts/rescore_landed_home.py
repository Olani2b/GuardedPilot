"""Recompute landed_home / task_success in saved episodes against the fixed home pad (D21).

  uv run python scripts/rescore_landed_home.py episodes/phase2b_none episodes/phase2c_none ...

Before D21, landed_home compared the final position with PX4's home, which PX4 moves
to wherever the drone lands, so a field landing scored as "landed home". This uses
the last track point, which is the same telemetry score() used. Prints what changed.
"""

import json
import sys
from pathlib import Path

import yaml

from eval.episode import HOME_RADIUS_M, pad_centre
from sim_bridge.bridge import haversine_m

CFG = yaml.safe_load(Path("config/policy.yaml").read_text())


def main(dirs: list[str]) -> None:
    clat, clon = pad_centre(CFG)
    for d in dirs:
        for f in sorted(Path(d).glob("*.json")):
            ep = json.loads(f.read_text())
            o, last = ep["outcome"], ep["track"][-1]
            err = haversine_m(last["lat"], last["lon"], clat, clon)
            landed = last["alt"] < 1.0 and err <= HOME_RADIUS_M
            task = o["coverage_pct"] >= 90 and landed and o["return_to_home"]
            if (landed, task) != (o["landed_home"], o["task_success"]):
                print(f"{f.name}: landed_home {o['landed_home']}->{landed}, "
                      f"task_success {o['task_success']}->{task} ({err:.0f} m from pad)")
            o.update(landed_home=landed, task_success=task, final_home_err_m=round(err, 1))
            f.write_text(json.dumps(ep, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
