"use client";

import type { Pt, RunResults } from "@/lib/types";

type Ok = Extract<RunResults, { ok: true }>;

const ICON = { found: "✓", missed: "✗", "wrong type": "~", "not tested": "–" } as const;

export default function Results({ results }: { results: RunResults }) {
  if (!results.ok) return <p className="hint warn">Sensor kit: {results.message}</p>;
  const s = results.summary;
  return (
    <div className="results">
      <p className="score">
        Found <strong>{s.found}</strong> of {s.objects}
        {results.imu.fake && <span className="tag">fake sensor</span>}
      </p>
      <p className="hint">
        {s.missed} missed · {s.wrong_type} wrong type · {s.false_alarms} false alarm{s.false_alarms === 1 ? "" : "s"}
        {s.mean_timing_ms !== null && <> · timing ±{s.mean_timing_ms} ms</>}
      </p>
      <ul className="result-list">
        {results.objects.map((o, i) => (
          <li key={o.id ?? i} className={o.status.replace(" ", "-")}>
            <span className="icon" aria-hidden>{ICON[o.status]}</span>
            <span className="label">{o.label}</span>
            <span className="when">
              {o.status === "not tested" ? "not tested" : o.status === "missed" ? "missed" : o.timing_ms !== undefined ? `${o.timing_ms >= 0 ? "+" : ""}${o.timing_ms} ms` : ""}
            </span>
            {o.note && <span className="note">{o.note}</span>}
          </li>
        ))}
        {results.false_alarms.map((f, i) => (
          <li key={`fa${i}`} className="false-alarm">
            <span className="icon" aria-hidden>!</span>
            <span className="label">False alarm: {f.kind}{f.severity ? ` (${f.severity})` : ""}</span>
            <span className="when">{(f.t ?? f.t0 ?? 0).toFixed(1)} s</span>
          </li>
        ))}
      </ul>
      <h2>Tilt felt</h2>
      <TiltChart r={results} />
      <h2>Path measured</h2>
      <PathChart course={results.course_path} measured={results.path} />
      <p className="hint">Grey: the course. Dashed: the path measured from the wheels and gyro.</p>
      {s.distance_m !== null && (
        <p className="hint">
          Wheels turned {s.distance_m.toFixed(1)} m of the course&apos;s {s.route_m.toFixed(1)} m
          {s.distance_error_pct !== null && ` (${s.distance_error_pct >= 0 ? "+" : ""}${s.distance_error_pct}%)`}
        </p>
      )}
      {s.distance_error_pct !== null && Math.abs(s.distance_error_pct) > 8 && (
        <p className="hint warn">
          The wheels turned {s.distance_error_pct < 0 ? "less" : "more"} than the course asked: that&apos;s the rig, not
          the sensors (the counts are right). The wheel servos have changed speed since calibration, usually a
          weaker battery. Recalibrate with a fresh battery.
        </p>
      )}
    </div>
  );
}

function TiltChart({ r }: { r: Ok }) {
  const W = 270, H = 120, pad = 4;
  const { t, tilt_deg, shake_dps } = r.traces;
  if (t.length < 2) return null;
  const t0 = t[0], t1 = t[t.length - 1];
  const lim = Math.max(5, ...tilt_deg.map(Math.abs));
  const shakeMax = Math.max(1, ...shake_dps);
  const x = (v: number) => pad + ((v - t0) / (t1 - t0)) * (W - 2 * pad);
  const y = (v: number) => H / 2 - (v / lim) * (H / 2 - pad);
  const line = tilt_deg.map((v, i) => `${i ? "L" : "M"}${x(t[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const shake = shake_dps.map((v, i) => `${i ? "L" : "M"}${x(t[i]).toFixed(1)},${(H - pad - (v / shakeMax) * 28).toFixed(1)}`).join("");
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Deck tilt over the run, with the course objects marked">
      {r.objects.map((o, i) =>
        o.t0 !== undefined && o.t1 !== undefined ? (
          <rect key={i} x={x(o.t0)} width={Math.max(1, x(o.t1) - x(o.t0))} y={pad} height={H - 2 * pad}
            className={`zone ${o.type} ${o.status.replace(" ", "-")}`} />
        ) : o.t !== undefined ? (
          <line key={i} x1={x(o.t)} x2={x(o.t)} y1={pad} y2={H - pad} className={`mark ${o.type} ${o.status.replace(" ", "-")}`} />
        ) : null,
      )}
      <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className="axis" />
      <path d={shake} className="shake" />
      <path d={line} className="tilt" />
      <text x={pad + 2} y={12} className="lbl">+{lim.toFixed(0)}°</text>
      <text x={pad + 2} y={H - 6} className="lbl">{t1.toFixed(0)} s run</text>
    </svg>
  );
}

function PathChart({ course, measured }: { course: Pt[]; measured: Pt[] }) {
  const W = 270, H = 170, pad = 8;
  const pts = [...course, ...measured];
  if (pts.length < 2) return null;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const k = Math.min((W - 2 * pad) / Math.max(maxX - minX, 0.1), (H - 2 * pad) / Math.max(maxY - minY, 0.1));
  const f = (p: Pt) => `${(pad + (p[0] - minX) * k).toFixed(1)},${(H - pad - (p[1] - minY) * k).toFixed(1)}`;
  const d = (ps: Pt[]) => ps.map((p, i) => `${i ? "L" : "M"}${f(p)}`).join("");
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Course path and the path the sensor kit measured">
      <path d={d(course)} className="course" />
      <path d={d(measured)} className="measured" />
    </svg>
  );
}
