"""Course geometry. Mirrors web/lib/course.ts.

Coordinates are metres on the sandbox plane, y up. Object positions in the course
file are distances `s` along the *raw* polyline (as drawn). The rig follows a
*rounded* version of the path (corners become arcs), so positions are converted
by projecting the raw point onto the rounded path.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

Point = tuple[float, float]


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


@dataclass
class Polyline:
    pts: list[Point]
    cum: list[float] = field(init=False)

    def __post_init__(self) -> None:
        if len(self.pts) < 2:
            raise ValueError("a path needs at least two points")
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + _dist(a, b))

    @property
    def length(self) -> float:
        return self.cum[-1]

    def _seg(self, s: float) -> int:
        s = min(max(s, 0.0), self.length)
        lo, hi = 0, len(self.cum) - 2
        while lo < hi:                      # binary search for the segment containing s
            mid = (lo + hi + 1) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def point_at(self, s: float) -> Point:
        s = min(max(s, 0.0), self.length)
        i = self._seg(s)
        a, b = self.pts[i], self.pts[i + 1]
        seg = self.cum[i + 1] - self.cum[i]
        r = 0.0 if seg == 0 else (s - self.cum[i]) / seg
        return (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r)

    def heading_at(self, s: float) -> float:
        """Direction of travel in radians, CCW from +x."""
        i = self._seg(s)
        a, b = self.pts[i], self.pts[i + 1]
        return math.atan2(b[1] - a[1], b[0] - a[0])

    def project(self, p: Point) -> float:
        """Distance along the path of the point closest to p."""
        best_s, best_d = 0.0, float("inf")
        for i, (a, b) in enumerate(zip(self.pts, self.pts[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
            q = (a[0] + dx * t, a[1] + dy * t)
            d = _dist(p, q)
            if d < best_d:
                best_d, best_s = d, self.cum[i] + t * math.sqrt(L2)
        return best_s


def rounded(pts: list[Point], radius: float, step: float = 0.05) -> list[Point]:
    """Replace each interior corner with a circular arc of `radius` (shrunk if the
    neighbouring segments are too short), sampled every `step` metres."""
    if radius <= 0 or len(pts) < 3:
        return list(pts)
    out: list[Point] = [pts[0]]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        a1 = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        a2 = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        turn = math.atan2(math.sin(a2 - a1), math.cos(a2 - a1))       # signed, CCW +
        if abs(turn) < 1e-3 or abs(abs(turn) - math.pi) < 1e-3:
            out.append(p1)
            continue
        tan_len = radius * math.tan(abs(turn) / 2)
        max_tan = 0.45 * min(_dist(p0, p1), _dist(p1, p2))
        r = radius if tan_len <= max_tan else max_tan / math.tan(abs(turn) / 2)
        tan_len = r * math.tan(abs(turn) / 2)
        start = (p1[0] - tan_len * math.cos(a1), p1[1] - tan_len * math.sin(a1))
        # centre is to the left of travel for a left (CCW) turn, right for a right turn
        side = 1 if turn > 0 else -1
        cx = start[0] - side * r * math.sin(a1)
        cy = start[1] + side * r * math.cos(a1)
        arc_len = r * abs(turn)
        n = max(2, int(arc_len / step))
        phi0 = math.atan2(start[1] - cy, start[0] - cx)
        for k in range(n + 1):
            phi = phi0 + turn * k / n
            out.append((cx + r * math.cos(phi), cy + r * math.sin(phi)))
    out.append(pts[-1])
    return out


@dataclass
class Course:
    raw: dict
    path: Polyline            # as drawn
    route: Polyline           # rounded corners: what the rig follows

    @staticmethod
    def load(src: str | Path | dict) -> "Course":
        raw = src if isinstance(src, dict) else json.loads(Path(src).read_text())
        pts = [(float(x), float(y)) for x, y in raw["path"]]
        path = Polyline(pts)
        route = Polyline(rounded(pts, float(raw.get("corner_radius_m", 0.5))))
        return Course(raw, path, route)

    def route_s(self, raw_s: float) -> float:
        """Convert a distance along the drawn path to one along the rounded route."""
        return self.route.project(self.path.point_at(raw_s))

    def max_abs_heading_deg(self) -> float:
        """Furthest the path ever points away from its starting direction (the yaw servo's range)."""
        total, prev, worst = 0.0, None, 0.0
        for a, b in zip(self.path.pts, self.path.pts[1:]):
            h = math.atan2(b[1] - a[1], b[0] - a[0])
            if prev is not None:
                total += math.atan2(math.sin(h - prev), math.cos(h - prev))
                worst = max(worst, abs(total))
            prev = h
        return math.degrees(worst)

    def net_turn_deg(self) -> float:
        total, prev = 0.0, None
        for a, b in zip(self.path.pts, self.path.pts[1:]):
            h = math.atan2(b[1] - a[1], b[0] - a[0])
            if prev is not None:
                total += math.atan2(math.sin(h - prev), math.cos(h - prev))
            prev = h
        return math.degrees(total)
