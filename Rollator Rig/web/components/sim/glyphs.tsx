import type { ObjectType } from "@/lib/types";

export const OBJECT_COLOURS: Record<ObjectType, string> = {
  crack: "var(--crack)",
  curb: "var(--curb)",
  slope: "var(--slope)",
  cross: "var(--cross)",
  rough: "var(--rough)",
};

export const PALETTE: { type: ObjectType; label: string; hint: string }[] = [
  { type: "crack", label: "Crack", hint: "One tap" },
  { type: "curb", label: "Curb", hint: "Stop, lift, drop" },
  { type: "slope", label: "Slope", hint: "Pitch zone" },
  { type: "cross", label: "Cross-slope", hint: "Roll zone" },
  { type: "rough", label: "Rough patch", hint: "Buzz zone" },
];

/** Point glyph drawn at (x, y) in SVG pixels. */
export function PointGlyph({ type, x, y, size = 1 }: { type: "crack" | "curb"; x: number; y: number; size?: number }) {
  const k = size;
  if (type === "crack")
    return (
      <polyline
        points={[[-7, -6], [-2, -1], [-4, 1], [3, 6], [1, 1], [6, -1]].map(([a, b]) => `${x + a * k},${y + b * k}`).join(" ")}
        fill="none"
        stroke={OBJECT_COLOURS.crack}
        strokeWidth={3}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    );
  return (
    <polygon
      points={`${x},${y - 8 * k} ${x - 7.5 * k},${y + 5 * k} ${x + 7.5 * k},${y + 5 * k}`}
      fill={OBJECT_COLOURS.curb}
      stroke="var(--bg)"
      strokeWidth={1.5}
    />
  );
}

/** Small icon used in the palette and the inspector. */
export function PaletteIcon({ type }: { type: ObjectType }) {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      {type === "crack" || type === "curb" ? (
        <PointGlyph type={type} x={11} y={12} size={0.9} />
      ) : (
        <line
          x1="3" y1="15" x2="19" y2="7"
          stroke={OBJECT_COLOURS[type]}
          strokeWidth={7}
          strokeLinecap="round"
          strokeDasharray={type === "rough" ? "2 3" : undefined}
          opacity={0.85}
        />
      )}
    </svg>
  );
}
