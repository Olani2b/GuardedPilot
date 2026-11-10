"""Image-library world (decision D1): a seeded, procedurally drawn aerial map.

Camera frames are crops of this map at the drone's pose, sized by altitude. Because
we draw the map, we know the ground truth for every pixel, which is what the
Phase 1 `segment_scene` stub returns. Scene-text attacks (A1) add `SceneText`
overlays to the world before an episode.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

HOME = (47.397971, 8.546164)
M_PER_PX = 0.25
SIZE_M = 400.0  # world is SIZE_M x SIZE_M centred on HOME
SIZE_PX = int(SIZE_M / M_PER_PX)
CAMERA_HFOV_DEG = 90.0
FRAME_PX = 256

CLASSES = {  # label -> (RGB in the map, UAVid-style class name)
    "field": ((122, 156, 74), "low_vegetation"),
    "field2": ((168, 150, 92), "low_vegetation"),
    "tree": ((46, 92, 46), "tree"),
    "road": ((96, 96, 100), "road"),
    "building": ((170, 80, 64), "building"),
    "car": ((40, 70, 190), "moving_car"),
    "pad": ((210, 210, 210), "landing_pad"),
}


def latlon_to_m(lat: float, lon: float) -> tuple[float, float]:
    """(east, north) metres from HOME."""
    n = (lat - HOME[0]) * 111_320.0
    e = (lon - HOME[1]) * 111_320.0 * math.cos(math.radians(HOME[0]))
    return e, n


def m_to_latlon(east: float, north: float) -> tuple[float, float]:
    lat = HOME[0] + north / 111_320.0
    lon = HOME[1] + east / (111_320.0 * math.cos(math.radians(HOME[0])))
    return lat, lon


def m_to_px(east: float, north: float) -> tuple[float, float]:
    return (east + SIZE_M / 2) / M_PER_PX, (SIZE_M / 2 - north) / M_PER_PX


@dataclass
class SceneText:
    """Text painted in the scene (A1). Position in lat/lon, size in metres."""

    lat: float
    lon: float
    text: str
    width_m: float = 14.0


@dataclass
class World:
    seed: int = 0
    texts: list[SceneText] = field(default_factory=list)
    cars: list[tuple[float, float]] = field(default_factory=list)  # (east, north) m

    def __post_init__(self):
        self._img: Image.Image | None = None
        self._labels: Image.Image | None = None  # palette-index ground truth

    # ---- rendering ---------------------------------------------------------

    def render(self) -> Image.Image:
        if self._img is not None:
            return self._img
        rng = random.Random(self.seed)
        img = Image.new("RGB", (SIZE_PX, SIZE_PX), CLASSES["field"][0])
        lab = Image.new("L", (SIZE_PX, SIZE_PX), 0)
        names = list(CLASSES)
        d, dl = ImageDraw.Draw(img), ImageDraw.Draw(lab)

        def rect(label: str, e0: float, n0: float, e1: float, n1: float):
            x0, y0 = m_to_px(e0, n1)
            x1, y1 = m_to_px(e1, n0)
            d.rectangle([x0, y0, x1, y1], fill=CLASSES[label][0])
            dl.rectangle([x0, y0, x1, y1], fill=names.index(label))

        def disc(label: str, e: float, n: float, r: float):
            x, y = m_to_px(e, n)
            rp = r / M_PER_PX
            d.ellipse([x - rp, y - rp, x + rp, y + rp], fill=CLASSES[label][0])
            dl.ellipse([x - rp, y - rp, x + rp, y + rp], fill=names.index(label))

        # Alternating crop strips.
        for i, e0 in enumerate(range(-200, 200, 40)):
            if i % 2:
                rect("field2", e0, -200, e0 + 40, 200)
        # Roads: one east-west, one north-south, crossing south-west of home.
        rect("road", -200, -30, 200, -22)
        rect("road", -60, -200, -52, 200)
        # Buildings along the roads.
        for _ in range(14):
            e = rng.uniform(-190, 190)
            n = rng.choice([rng.uniform(-60, -36), rng.uniform(-16, 10)])
            if abs(e) < 15 and n > -20:  # keep the pad clear
                continue
            w, h = rng.uniform(8, 18), rng.uniform(8, 14)
            rect("building", e, n, e + w, n + h)
        # Tree clusters.
        for _ in range(40):
            disc("tree", rng.uniform(-200, 200), rng.uniform(40, 200), rng.uniform(2, 5))
        # Cars, parked along the east-west road (ground truth kept for segment_scene).
        if not self.cars:
            self.cars = [(rng.uniform(-180, 180), rng.choice([-33.0, -19.0])) for _ in range(10)]
        for e, n in self.cars:
            rect("car", e - 2.2, n - 1.0, e + 2.2, n + 1.0)
        # Landing pad at home with an H.
        rect("pad", -4, -4, 4, 4)
        x, y = m_to_px(0, 0)
        d.text((x, y), "H", fill=(30, 30, 30), anchor="mm", font=_font(int(6 / M_PER_PX)))
        # A1 scene text overlays (not a class: painted on whatever is underneath).
        for t in self.texts:
            self._paint_text(d, t)
        self._img, self._labels = img, lab
        return img

    def _paint_text(self, d: ImageDraw.ImageDraw, t: SceneText) -> None:
        e, n = latlon_to_m(t.lat, t.lon)
        x, y = m_to_px(e, n)
        w_px = t.width_m / M_PER_PX
        lines = _wrap(t.text, 28)
        size = max(6, int(w_px / 14))
        f = _font(size)
        body = "\n".join(lines)
        bx0, by0, bx1, by1 = d.multiline_textbbox((x, y), body, anchor="mm", font=f, align="center")
        pad = size * 0.6
        d.rectangle([bx0 - pad, by0 - pad, bx1 + pad, by1 + pad], fill=(245, 245, 235))
        d.multiline_text((x, y), body, fill=(10, 10, 10), anchor="mm", font=f, align="center")

    # ---- camera ------------------------------------------------------------

    def footprint_m(self, alt_m: float) -> float:
        """Side length (m) of the square ground footprint of a nadir camera."""
        return 2 * max(alt_m, 1.0) * math.tan(math.radians(CAMERA_HFOV_DEG / 2))

    def frame(self, lat: float, lon: float, alt_m: float) -> tuple[bytes, dict]:
        """PNG of the downward view at this pose, plus ground-truth stats."""
        img = self.render()
        e, n = latlon_to_m(lat, lon)
        side = self.footprint_m(alt_m)
        x0, y0 = m_to_px(e - side / 2, n + side / 2)
        x1, y1 = m_to_px(e + side / 2, n - side / 2)
        box = tuple(round(v) for v in (x0, y0, x1, y1))
        crop = img.crop(box).resize((FRAME_PX, FRAME_PX), Image.BILINEAR)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue(), self._truth(box, e, n, side)

    def texts_in_view(self, lat: float, lon: float, alt_m: float) -> list[SceneText]:
        """Scene texts whose centre lies inside the camera footprint at this pose."""
        e, n = latlon_to_m(lat, lon)
        half = self.footprint_m(alt_m) / 2
        out = []
        for t in self.texts:
            te, tn = latlon_to_m(t.lat, t.lon)
            if abs(te - e) <= half and abs(tn - n) <= half:
                out.append(t)
        return out

    def _truth(self, box, e: float, n: float, side: float) -> dict:
        lab = self._labels.crop(box)
        hist = lab.histogram()[: len(CLASSES)]
        total = max(sum(hist), 1)
        frac: dict[str, float] = {}
        for (label, (_, cls)), count in zip(CLASSES.items(), hist):
            frac[cls] = round(frac.get(cls, 0) + count / total, 3)
        half = side / 2
        cars = sum(1 for ce, cn in self.cars if abs(ce - e) <= half and abs(cn - n) <= half)
        pad_visible = frac.get("landing_pad", 0) > 0
        return {"class_fractions": frac, "vehicles": cars, "landing_pad_visible": pad_visible}


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])
