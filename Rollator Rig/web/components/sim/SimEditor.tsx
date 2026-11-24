"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { checkCourse, DEFAULT_DECK, EMPTY_COURSE, EXAMPLE_COURSE, objectLabel, pathLength } from "@/lib/course";
import type { DeckSettings } from "@/lib/course";
import type { Course, CourseObject, ObjectType, Pt, Run, RunResults } from "@/lib/types";
import { isZone } from "@/lib/types";
import Plane, { type DragNew, type Mode, type PlaneApi } from "./Plane";
import { PALETTE, PaletteIcon } from "./glyphs";
import Results from "./Results";

const DRAFT_KEY = "rig-sandbox-draft";
const FINISHED: Run["status"][] = ["done", "error", "aborted"];

type Saved = { id: number; name: string; created_at: string; course: Course };

function newObject(type: ObjectType, s: number, length: number): CourseObject {
  const id = `${type[0]}${Date.now().toString(36)}${Math.floor(Math.random() * 1e3)}`;
  const r = (v: number) => Math.round(v * 100) / 100;
  const s0 = r(Math.max(0, s - 1));
  const s1 = r(Math.min(length, s + 1));
  switch (type) {
    case "crack": return { id, type, s: r(s), severity: "large" };
    case "curb": return { id, type, s: r(s), height: "high" };
    case "slope": return { id, type, s0, s1, grade_pct: 8 };
    case "cross": return { id, type, s0, s1, tilt_pct: 4 };
    case "rough": return { id, type, s0, s1, level: "light" };
  }
}

const fmtTime = (s: number) => `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;

export default function SimEditor() {
  const [course, setCourse] = useState<Course>(EXAMPLE_COURSE);
  const [mode, setMode] = useState<Mode>("edit");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dragNew, setDragNew] = useState<DragNew>(null);
  const [saved, setSaved] = useState<Saved[]>([]);
  const [savedId, setSavedId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [results, setResults] = useState<{ id: number; data: RunResults } | null>(null);
  const [queuedSince, setQueuedSince] = useState<number | null>(null);
  const planeApi = useRef<PlaneApi | null>(null);

  const length = pathLength(course.path);
  const [deck, setDeck] = useState<DeckSettings>(DEFAULT_DECK);
  // deck trims (data/deck.json) change how far the yaw servo can turn; re-read when the tab regains focus
  useEffect(() => {
    const load = () =>
      fetch("/api/rig")
        .then((r) => (r.ok ? r.json() : null))
        .then((d: DeckSettings | null) => d && setDeck(d))
        .catch(() => {});
    load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, []);
  const { checks, runTimeS, peakRps } = useMemo(() => checkCourse(course, deck), [course, deck]);
  const hasError = checks.some((c) => c.level === "error");
  const selected = course.objects.find((o) => o.id === selectedId) ?? null;
  const live = run !== null && !FINISHED.includes(run.status);
  // the driver reports distance along the rounded route, slightly shorter than the drawn path
  const runS = run ? (run.status === "done" ? length : Math.min(run.progress_s, length)) : 0;

  // ---- draft persistence (per browser, best effort)
  useEffect(() => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (raw) setCourse(JSON.parse(raw));
    } catch {
      /* private mode or bad JSON: keep the example */
    }
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(course));
    } catch {
      /* ignore */
    }
  }, [course]);

  const refreshSaved = useCallback(async () => {
    const res = await fetch("/api/courses");
    if (res.ok) setSaved(await res.json());
  }, []);
  useEffect(() => {
    refreshSaved();
  }, [refreshSaved]);

  // ---- run polling
  useEffect(() => {
    if (!run || FINISHED.includes(run.status)) return;
    const t = setInterval(async () => {
      const res = await fetch(`/api/runs/${run.id}`);
      if (res.ok) setRun(await res.json());
    }, 400);
    return () => clearInterval(t);
  }, [run]);

  // ---- results: once a run is done, fetch what the sensor kit detected (404 = no ESP32 recorded)
  const doneId = run?.status === "done" ? run.id : null;
  useEffect(() => {
    if (doneId === null) return;
    let cancelled = false;
    fetch(`/api/runs/${doneId}/results`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: RunResults | null) => {
        if (!cancelled && data) setResults({ id: doneId, data });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [doneId]);
  const runResults = results && run && results.id === run.id ? results.data : null;

  // ---- course updates
  const update = (patch: Partial<Course>) => setCourse((c) => ({ ...c, ...patch }));
  const clearFinishedRun = () => setRun((r) => (r && FINISHED.includes(r.status) ? null : r));
  const setPath = (path: Pt[]) => {
    clearFinishedRun();
    setCourse((c) => {
      const L = pathLength(path);
      return {
        ...c,
        path,
        objects: c.objects.map((o) =>
          isZone(o) ? { ...o, s0: Math.min(o.s0, L), s1: Math.min(o.s1, L) } : { ...o, s: Math.min(o.s, L) },
        ),
      };
    });
  };
  const setObjects = (objects: CourseObject[]) => update({ objects });
  const patchSelected = (patch: Partial<CourseObject>) =>
    setObjects(course.objects.map((o) => (o.id === selectedId ? ({ ...o, ...patch } as CourseObject) : o)));
  const deleteSelected = () => {
    setObjects(course.objects.filter((o) => o.id !== selectedId));
    setSelectedId(null);
  };

  // keyboard: Enter/Escape finish drawing, Delete removes the selected object
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select, textarea")) return;
      if (mode === "draw" && (e.key === "Enter" || e.key === "Escape")) setMode("edit");
      if (mode === "edit" && selectedId && (e.key === "Delete" || e.key === "Backspace")) deleteSelected();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ---- drag from the palette onto the path
  const startPaletteDrag = (type: ObjectType) => (e: React.PointerEvent) => {
    if (course.path.length < 2) {
      setNotice("Draw a path first, then drag objects onto it.");
      return;
    }
    e.preventDefault();
    setMode("edit");
    setDragNew({ type, clientX: e.clientX, clientY: e.clientY });
  };
  const dragRef = useRef<DragNew>(null);
  dragRef.current = dragNew;
  useEffect(() => {
    if (!dragNew) return;
    const move = (e: PointerEvent) => setDragNew((d) => (d ? { ...d, clientX: e.clientX, clientY: e.clientY } : d));
    const up = (e: PointerEvent) => {
      const d = dragRef.current;
      const s = planeApi.current?.sAtClient(e.clientX, e.clientY) ?? null;
      if (d && s !== null) {
        const obj = newObject(d.type, s, pathLength(course.path));
        setCourse((c) => ({ ...c, objects: [...c.objects, obj] }));
        setSelectedId(obj.id);
      } else if (d) {
        setNotice("Drop objects onto the path (within 2.5 m of it).");
      }
      setDragNew(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [dragNew !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- toolbar actions
  const startDrawing = () => {
    clearFinishedRun();
    setCourse((c) => ({ ...c, path: [], objects: [] }));
    setSelectedId(null);
    setMode("draw");
  };
  const save = async () => {
    const res = await fetch("/api/courses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course }),
    });
    if (res.ok) {
      const { id } = await res.json();
      setSavedId(id);
      setNotice(`Saved as course ${id}.`);
      refreshSaved();
    } else setNotice("Could not save the course.");
  };
  const load = (id: number) => {
    const s = saved.find((x) => x.id === id);
    if (!s) return;
    clearFinishedRun();
    setCourse(s.course);
    setSavedId(id);
    setSelectedId(null);
    setMode("edit");
  };
  const exportJson = () => {
    const blob = new Blob([JSON.stringify(course, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${course.name.replace(/[^\w-]+/g, "_") || "course"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const startRun = async () => {
    setNotice(null);
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course, courseId: savedId }),
    });
    const body = await res.json();
    if (!res.ok) {
      setNotice(body.error ?? "Could not start the run.");
      return;
    }
    setQueuedSince(Date.now());
    setRun({ id: body.id, status: "queued", progress_s: 0, duration_s: null, message: null, created_at: "", started_at: null, finished_at: null });
  };
  const abortRun = async () => {
    if (run) await fetch(`/api/runs/${run.id}/abort`, { method: "POST" });
  };

  const driverIdle = run?.status === "queued" && queuedSince !== null && Date.now() - queuedSince > 3000;

  return (
    <div className="sim">
      <header className="sim-header">
        <div className="title">
          <h1>Rig sandbox</h1>
          <input
            aria-label="Course name"
            value={course.name}
            onChange={(e) => update({ name: e.target.value })}
            className="name-input"
          />
        </div>
        <div className="actions">
          <button onClick={() => { clearFinishedRun(); setCourse(EMPTY_COURSE); setSelectedId(null); setMode("draw"); setSavedId(null); }}>New</button>
          <button onClick={() => { clearFinishedRun(); setCourse(EXAMPLE_COURSE); setSelectedId(null); setMode("edit"); setSavedId(null); }}>Example</button>
          <select aria-label="Load a saved course" value="" onChange={(e) => load(Number(e.target.value))}>
            <option value="" disabled>Load saved…</option>
            {saved.map((s) => (
              <option key={s.id} value={s.id}>{s.id}: {s.name}</option>
            ))}
          </select>
          <button onClick={save}>Save</button>
          <button onClick={exportJson}>Export JSON</button>
          {live ? (
            <button className="danger" onClick={abortRun}>Abort run</button>
          ) : (
            <button className="primary" disabled={hasError} onClick={startRun}>Run on rig</button>
          )}
        </div>
      </header>

      {notice && (
        <div className="notice" role="status">
          {notice}
          <button aria-label="Dismiss" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <div className="sim-body">
        <aside className="panel tools">
          <h2>Path</h2>
          <div className="segmented">
            <button className={mode === "draw" ? "on" : ""} onClick={() => setMode("draw")}>Draw</button>
            <button className={mode === "edit" ? "on" : ""} onClick={() => setMode("edit")}>Edit</button>
          </div>
          <div className="row">
            <button onClick={startDrawing}>Redraw</button>
            {mode === "draw" && <button className="primary" onClick={() => setMode("edit")}>Finish path</button>}
          </div>
          <p className="hint">
            {mode === "draw"
              ? "Click to add points (snaps to 0.5 m). Double-click or press Enter to finish."
              : "Drag points to reshape. Alt-click a point to remove it. Drag objects along the path."}
          </p>

          <h2>Objects</h2>
          <p className="hint">Drag onto the path.</p>
          <ul className="palette">
            {PALETTE.map((p) => (
              <li key={p.type}>
                <button className="chip" onPointerDown={startPaletteDrag(p.type)}>
                  <PaletteIcon type={p.type} />
                  <span>
                    <strong>{p.label}</strong>
                    <small>{p.hint}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="plane-wrap">
          <Plane
            course={course}
            mode={mode}
            selectedId={selectedId}
            dragNew={dragNew}
            rigS={run && run.status !== "queued" ? runS : null}
            apiRef={planeApi}
            onPath={setPath}
            onObjects={setObjects}
            onSelect={setSelectedId}
            onFinishPath={() => setMode("edit")}
          />
        </main>

        <aside className="panel inspector">
          {selected ? (
            <section>
              <h2><PaletteIcon type={selected.type} /> {objectLabel(selected)}</h2>
              {"s" in selected ? (
                <Field label="Position along path (m)">
                  <input type="number" step={0.1} min={0} max={length} value={selected.s}
                    onChange={(e) => patchSelected({ s: Number(e.target.value) })} />
                </Field>
              ) : (
                <>
                  <Field label="Starts at (m)">
                    <input type="number" step={0.1} min={0} value={selected.s0}
                      onChange={(e) => patchSelected({ s0: Number(e.target.value) })} />
                  </Field>
                  <Field label="Ends at (m)">
                    <input type="number" step={0.1} min={0} value={selected.s1}
                      onChange={(e) => patchSelected({ s1: Number(e.target.value) })} />
                  </Field>
                </>
              )}
              {selected.type === "crack" && (
                <Field label="Severity">
                  <select value={selected.severity} onChange={(e) => patchSelected({ severity: e.target.value as "small" | "large" })}>
                    <option value="small">Small</option>
                    <option value="large">Large</option>
                  </select>
                </Field>
              )}
              {selected.type === "curb" && (
                <Field label="Height">
                  <select value={selected.height} onChange={(e) => patchSelected({ height: e.target.value as "low" | "high" })}>
                    <option value="low">Low</option>
                    <option value="high">High</option>
                  </select>
                </Field>
              )}
              {selected.type === "slope" && (
                <Field label="Grade (%), negative = downhill">
                  <input type="number" step={0.5} value={selected.grade_pct}
                    onChange={(e) => patchSelected({ grade_pct: Number(e.target.value) })} />
                </Field>
              )}
              {selected.type === "cross" && (
                <Field label="Tilt (%), negative = left side low">
                  <input type="number" step={0.5} value={selected.tilt_pct}
                    onChange={(e) => patchSelected({ tilt_pct: Number(e.target.value) })} />
                </Field>
              )}
              {selected.type === "rough" && (
                <Field label="Roughness">
                  <select value={selected.level} onChange={(e) => patchSelected({ level: e.target.value as "light" | "heavy" })}>
                    <option value="light">Light</option>
                    <option value="heavy">Heavy</option>
                  </select>
                </Field>
              )}
              <button className="danger" onClick={deleteSelected}>Delete</button>
            </section>
          ) : (
            <section>
              <h2>Course settings</h2>
              <Field label="Walker speed (m/s)">
                <input type="number" step={0.05} min={0.1} value={course.walker.speed_mps}
                  onChange={(e) => update({ walker: { ...course.walker, speed_mps: Number(e.target.value) } })} />
              </Field>
              <Field label="Wheel circumference (m)">
                <input type="number" step={0.01} min={0.1} value={course.walker.wheel_circ_m}
                  onChange={(e) => update({ walker: { ...course.walker, wheel_circ_m: Number(e.target.value) } })} />
              </Field>
              <Field label="Track width (m)">
                <input type="number" step={0.01} min={0} value={course.walker.track_m}
                  onChange={(e) => update({ walker: { ...course.walker, track_m: Number(e.target.value) } })} />
              </Field>
              <Field label="Corner radius (m)">
                <input type="number" step={0.1} min={0} value={course.corner_radius_m}
                  onChange={(e) => update({ corner_radius_m: Number(e.target.value) })} />
              </Field>
              <Field label="Time scale (rig runs this many times slower)">
                <input type="number" step={0.5} min={1} value={course.rig.time_scale}
                  onChange={(e) => update({ rig: { time_scale: Number(e.target.value) } })} />
              </Field>
            </section>
          )}

          <section>
            <h2>Checks</h2>
            <dl className="stats">
              <dt>Path length</dt><dd>{length.toFixed(1)} m</dd>
              <dt>Rig run time</dt><dd>{runTimeS ? fmtTime(runTimeS) : "–"}</dd>
              <dt>Peak wheel speed</dt><dd>{peakRps.toFixed(2)} turns/s</dd>
              <dt>Objects</dt><dd>{course.objects.length}</dd>
            </dl>
            <ul className="checks">
              {checks.map((c, i) => (
                <li key={i} className={c.level}>{c.text}</li>
              ))}
            </ul>
          </section>

          {run && (
            <section>
              <h2>Run {run.id}</h2>
              <p className={`status ${run.status}`}>{run.status}</p>
              <div className="progress" aria-label="Run progress">
                <div style={{ width: `${(runS / Math.max(length, 0.01)) * 100}%` }} />
              </div>
              <p className="hint">{runS.toFixed(1)} of {length.toFixed(1)} m</p>
              {run.message && !runResults && <p className="hint">{run.message}</p>}
              {runResults && <Results results={runResults} />}
              {driverIdle && (
                <p className="hint warn">
                  Waiting for the driver. Start it with:
                  <code>python pipeline/rig_driver.py watch --port COM3</code>
                  (or <code>--fake</code> to test without the rig)
                </p>
              )}
            </section>
          )}
        </aside>
      </div>

      {dragNew && (
        <div className="drag-ghost" style={{ left: dragNew.clientX, top: dragNew.clientY }}>
          <PaletteIcon type={dragNew.type} />
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
