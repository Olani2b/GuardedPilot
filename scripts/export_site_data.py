"""Copy recorded episodes into the static site and render the world map(s).

  uv run python scripts/export_site_data.py [episodes_dir]

Writes site/public/data/:
  index.json                 list of episodes with outcome summary
  episodes/<id>.json         episode JSON (unchanged)
  episodes/<id>/fNNN.png     camera frames
  worlds/<key>.png + bounds  one map per distinct world (seed + scene text)
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

from perception.world import SIZE_M, SceneText, World, m_to_latlon

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "episodes")
OUT = Path("site/public/data")


def world_key(w: dict) -> str:
    return hashlib.sha1(json.dumps(w, sort_keys=True).encode()).hexdigest()[:10]


def main():
    (OUT / "episodes").mkdir(parents=True, exist_ok=True)
    (OUT / "worlds").mkdir(parents=True, exist_ok=True)
    south_west = m_to_latlon(-SIZE_M / 2, -SIZE_M / 2)
    north_east = m_to_latlon(SIZE_M / 2, SIZE_M / 2)
    index, worlds = [], {}
    for path in sorted(SRC.glob("*.json")):
        d = json.loads(path.read_text())
        w = d.get("world", {"seed": 0, "texts": []})
        key = world_key(w)
        if key not in worlds:
            world = World(seed=w["seed"], texts=[SceneText(**t) for t in w["texts"]])
            world.render().save(OUT / "worlds" / f"{key}.png", optimize=True)
            worlds[key] = True
        d["world_image"] = {"src": f"data/worlds/{key}.png", "bounds": [south_west, north_east]}
        (OUT / "episodes" / path.name).write_text(json.dumps(d))
        frames = SRC / d["id"]
        if frames.is_dir():
            shutil.copytree(frames, OUT / "episodes" / d["id"], dirs_exist_ok=True)
        index.append({"id": d["id"], "scenario": d["scenario"], "model": d["model"],
                      "defense": d["defense"], "outcome": d["outcome"],
                      "wall_s": d["meta"].get("wall_s"), "tokens": d["meta"].get("tokens"),
                      "finished_at": d["meta"].get("finished_at")})
    (OUT / "index.json").write_text(json.dumps({"episodes": index}, indent=1))
    print(f"exported {len(index)} episodes, {len(worlds)} world map(s) -> {OUT}")


if __name__ == "__main__":
    main()
