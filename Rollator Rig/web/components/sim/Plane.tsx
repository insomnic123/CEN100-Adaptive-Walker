"use client";

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { pathLength, pointAt, project, subpath } from "@/lib/course";
import type { Course, CourseObject, ObjectType, Pt } from "@/lib/types";
import { isZone } from "@/lib/types";
import { OBJECT_COLOURS, PointGlyph } from "./glyphs";

export const WORLD_M = 30; // the sandbox is 30 m x 30 m
const PX = 20; // SVG pixels per metre
const SIZE = WORLD_M * PX;
const SNAP_M = 0.5;
const DROP_RANGE_M = 2.5; // how close to the path a dropped object must land

export type PlaneApi = { sAtClient: (x: number, y: number) => number | null };
export type Mode = "draw" | "edit";
export type DragNew = { type: ObjectType; clientX: number; clientY: number } | null;

type Drag =
  | { kind: "vertex"; i: number }
  | { kind: "object"; id: string; part: "whole" | "s0" | "s1"; grab: number }
  | null;

const toPx = ([x, y]: Pt): Pt => [x * PX, (WORLD_M - y) * PX];
const snap = (v: number) => Math.round(v / SNAP_M) * SNAP_M;
const clampW = (v: number) => Math.min(WORLD_M, Math.max(0, v));
const polyPts = (pts: Pt[]) => pts.map((p) => toPx(p).join(",")).join(" ");

export default function Plane(props: {
  course: Course;
  mode: Mode;
  selectedId: string | null;
  dragNew: DragNew;
  rigS: number | null;
  apiRef: MutableRefObject<PlaneApi | null>;
  onPath: (path: Pt[]) => void;
  onObjects: (objects: CourseObject[]) => void;
  onSelect: (id: string | null) => void;
  onFinishPath: () => void;
}) {
  const { course, mode, selectedId, dragNew, rigS, apiRef } = props;
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<Pt | null>(null);
  const [drag, setDrag] = useState<Drag>(null);
  const path = course.path;
  const L = pathLength(path);

  const toWorld = (clientX: number, clientY: number): Pt | null => {
    const svg = svgRef.current;
    const m = svg?.getScreenCTM();
    if (!svg || !m) return null;
    const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
    return [p.x / PX, WORLD_M - p.y / PX];
  };
  const inside = (clientX: number, clientY: number) => {
    const r = svgRef.current?.getBoundingClientRect();
    return !!r && clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom;
  };
  const sNear = (w: Pt | null) => {
    if (!w || path.length < 2) return null;
    const { s, d } = project(path, w);
    return d <= DROP_RANGE_M ? s : null;
  };
  useEffect(() => {
    apiRef.current = { sAtClient: (x, y) => (inside(x, y) ? sNear(toWorld(x, y)) : null) };
  });

  // ---- pointer handling
  const onBackgroundDown = (e: React.PointerEvent) => {
    if (mode === "draw") {
      const w = toWorld(e.clientX, e.clientY);
      if (!w) return;
      const p: Pt = [clampW(snap(w[0])), clampW(snap(w[1]))];
      const last = path.at(-1);
      if (last && Math.hypot(last[0] - p[0], last[1] - p[1]) < 0.01) return;
      props.onPath([...path, p]);
    } else {
      props.onSelect(null);
    }
  };

  const startVertex = (i: number) => (e: React.PointerEvent) => {
    if (mode !== "edit") return;
    e.stopPropagation();
    if (e.altKey) {
      // Alt-click removes a point
      if (path.length > 2) props.onPath(path.filter((_, j) => j !== i));
      return;
    }
    (e.target as Element).setPointerCapture(e.pointerId);
    setDrag({ kind: "vertex", i });
  };

  const startObject = (o: CourseObject, part: "whole" | "s0" | "s1") => (e: React.PointerEvent) => {
    if (mode !== "edit") return;
    e.stopPropagation();
    props.onSelect(o.id);
    (e.target as Element).setPointerCapture(e.pointerId);
    const s = sNear(toWorld(e.clientX, e.clientY)) ?? 0;
    const anchor = "s" in o ? o.s : part === "whole" ? (o.s0 + o.s1) / 2 : 0;
    setDrag({ kind: "object", id: o.id, part, grab: s - anchor });
  };

  const onMove = (e: React.PointerEvent) => {
    const w = toWorld(e.clientX, e.clientY);
    setHover(w);
    if (!drag || !w) return;
    if (drag.kind === "vertex") {
      const next = path.map((p, j): Pt => (j === drag.i ? [clampW(snap(w[0])), clampW(snap(w[1]))] : p));
      props.onPath(next);
      return;
    }
    const { s } = project(path, w);
    props.onObjects(
      course.objects.map((o) => {
        if (o.id !== drag.id) return o;
        if (!isZone(o)) return { ...o, s: round(clamp(s - drag.grab, 0, L)) };
        if (drag.part === "s0") return { ...o, s0: round(clamp(s, 0, o.s1 - 0.2)) };
        if (drag.part === "s1") return { ...o, s1: round(clamp(s, o.s0 + 0.2, L)) };
        const half = (o.s1 - o.s0) / 2;
        const mid = clamp(s - drag.grab, half, L - half);
        return { ...o, s0: round(mid - half), s1: round(mid + half) };
      }),
    );
  };

  const onUp = () => setDrag(null);

  // ---- drawing
  const grid = [];
  for (let i = 0; i <= WORLD_M; i++) {
    const major = i % 5 === 0;
    grid.push(
      <line key={`v${i}`} x1={i * PX} y1={0} x2={i * PX} y2={SIZE} className={major ? "grid-major" : "grid"} />,
      <line key={`h${i}`} x1={0} y1={i * PX} x2={SIZE} y2={i * PX} className={major ? "grid-major" : "grid"} />,
    );
  }

  const zones = course.objects.filter(isZone);
  const points = course.objects.filter((o) => !isZone(o)) as Exclude<CourseObject, { s0: number }>[];
  const previewS = dragNew ? sNear(toWorld(dragNew.clientX, dragNew.clientY)) : null;
  const drawingPreview = mode === "draw" && hover && path.length > 0;

  return (
    <svg
      ref={svgRef}
      className={`plane mode-${mode}`}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      role="img"
      aria-label="Sandbox plane: 30 by 30 metre grid with the drawn course"
      onPointerDown={onBackgroundDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerLeave={() => setHover(null)}
      onDoubleClick={() => mode === "draw" && props.onFinishPath()}
    >
      <rect width={SIZE} height={SIZE} className="plane-bg" />
      {grid}
      {[5, 10, 15, 20, 25].map((m) => (
        <text key={m} x={m * PX + 3} y={SIZE - 4} className="axis-label">{m} m</text>
      ))}

      {zones.map((z) => {
        const pts = subpath(path, z.s0, z.s1);
        const sel = z.id === selectedId;
        return (
          <g key={z.id}>
            {sel && <polyline points={polyPts(pts)} className="zone-selected" />}
            <polyline
              points={polyPts(pts)}
              className="zone"
              stroke={OBJECT_COLOURS[z.type]}
              strokeDasharray={z.type === "rough" ? "3 5" : undefined}
              onPointerDown={startObject(z, "whole")}
            />
          </g>
        );
      })}

      {path.length > 1 && <polyline points={polyPts(path)} className="path" />}
      {drawingPreview && (
        <line
          x1={toPx(path.at(-1)!)[0]} y1={toPx(path.at(-1)!)[1]}
          x2={toPx([snap(hover[0]), snap(hover[1])])[0]} y2={toPx([snap(hover[0]), snap(hover[1])])[1]}
          className="path-preview"
        />
      )}
      {path.map((p, i) => {
        const [x, y] = toPx(p);
        return (
          <circle
            key={i}
            cx={x} cy={y} r={i === 0 ? 7 : 5}
            className={i === 0 ? "vertex start" : "vertex"}
            onPointerDown={startVertex(i)}
          />
        );
      })}

      {zones.map((z) => {
        if (z.id !== selectedId || mode !== "edit") return null;
        return (["s0", "s1"] as const).map((part) => {
          const [x, y] = toPx(pointAt(path, z[part]));
          return <circle key={part} cx={x} cy={y} r={6} className="handle" onPointerDown={startObject(z, part)} />;
        });
      })}

      {points.map((o) => {
        const [x, y] = toPx(pointAt(path, o.s));
        return (
          <g key={o.id} className="point-object" onPointerDown={startObject(o, "whole")}>
            <circle cx={x} cy={y} r={13} className={o.id === selectedId ? "hit selected" : "hit"} />
            <PointGlyph type={o.type} x={x} y={y} size={o.type === "crack" && o.severity === "large" ? 1.3 : 1} />
          </g>
        );
      })}

      {dragNew && previewS !== null && (() => {
        const [x, y] = toPx(pointAt(path, previewS));
        return (
          <g opacity={0.75} pointerEvents="none">
            <circle cx={x} cy={y} r={14} className="drop-target" />
            {dragNew.type === "crack" || dragNew.type === "curb" ? (
              <PointGlyph type={dragNew.type} x={x} y={y} />
            ) : (
              <polyline
                points={polyPts(subpath(path, Math.max(0, previewS - 1), Math.min(L, previewS + 1)))}
                className="zone"
                stroke={OBJECT_COLOURS[dragNew.type]}
              />
            )}
          </g>
        );
      })()}

      {rigS !== null && path.length > 1 && (() => {
        const [x, y] = toPx(pointAt(path, rigS));
        return (
          <g pointerEvents="none">
            <circle cx={x} cy={y} r={11} className="rig-halo" />
            <circle cx={x} cy={y} r={6} className="rig-dot" />
          </g>
        );
      })()}
    </svg>
  );
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const round = (v: number) => Math.round(v * 100) / 100;
