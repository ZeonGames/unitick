import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type JobState } from "../api";
import { Problem } from "../components/Status";

const ACCEPT = [".dem", ".vrf", ".rec"];
const STEPS: { state: JobState[]; label: string }[] = [
  { state: ["uploading"], label: "Upload" },
  { state: ["queued", "parsing"], label: "Read the replay" },
  { state: ["analyzing"], label: "Analyze" },
  { state: ["done"], label: "Open report" },
];

function size(n: number) {
  return n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(0)} MB` : `${Math.ceil(n / 1e3)} KB`;
}

function validate(files: File[]): string | null {
  if (!files.length) return "Choose a replay file.";
  const bad = files.find((f) => !ACCEPT.some((ext) => f.name.toLowerCase().endsWith(ext)));
  if (bad) return `${bad.name} is not a replay. Use a CS2 .dem, a VALORANT .vrf, or the .rec files of one Siege match.`;
  const exts = new Set(files.map((f) => f.name.slice(f.name.lastIndexOf(".")).toLowerCase()));
  if (exts.size > 1) return "Upload one match at a time: a single .dem or .vrf, or all .rec files of one Siege match.";
  if (!exts.has(".rec") && files.length > 1) return "Upload one .dem or .vrf at a time.";
  return null;
}

export default function UploadPage() {
  const nav = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [over, setOver] = useState(false);
  const [state, setState] = useState<JobState | null>(null);
  const [sent, setSent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const total = files.reduce((n, f) => n + f.size, 0);
  const busy = state !== null && state !== "failed" && state !== "done";

  const pick = useCallback((list: FileList | null) => {
    const next = Array.from(list ?? []);
    setFiles(next);
    setError(next.length ? validate(next) : null);
    setState(null);
  }, []);

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (!busy) pick(e.dataTransfer.files);
  }

  async function start() {
    const problem = validate(files);
    if (problem) { setError(problem); return; }
    setError(null);
    setState("uploading");
    try {
      const job = await api.createJob();
      let done = 0;
      for (const f of files) {
        await api.uploadFile(job.id, f, (n) => setSent(done + n));
        done += f.size;
      }
      setSent(total);
      await api.runJob(job.id);
      setState("queued");
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        const j = await api.job(job.id);
        setState(j.state);
        if (j.state === "done" && j.match_id) { nav(`/match/${j.match_id}`); return; }
        if (j.state === "failed") { setError(j.error ?? "This replay could not be analyzed."); return; }
      }
    } catch (e) {
      setState("failed");
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    const block = (e: Event) => e.preventDefault();
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => { window.removeEventListener("dragover", block); window.removeEventListener("drop", block); };
  }, []);

  const stepIndex = state ? STEPS.findIndex((s) => s.state.includes(state)) : -1;

  return (
    <main className="page narrow">
      <h1>Analyze a replay</h1>
      <p className="lede">
        Drop a replay your game saved. It is read on this PC and never leaves it.
      </p>

      <div
        className={`drop${over ? " over" : ""}${busy ? " busy" : ""}`}
        onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => !busy && input.current?.click()}
        onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && !busy) input.current?.click(); }}
        role="button"
        tabIndex={0}
        aria-label="Choose replay files"
      >
        <input ref={input} type="file" accept={ACCEPT.join(",")} multiple hidden onChange={(e) => pick(e.target.files)} />
        {files.length ? (
          <ul className="files">
            {files.map((f) => <li key={f.name}><b>{f.name}</b><span>{size(f.size)}</span></li>)}
          </ul>
        ) : (
          <>
            <b>Drop a replay here, or click to choose one</b>
            <span>CS2 .dem, VALORANT .vrf, or all .rec files of one Siege match</span>
          </>
        )}
      </div>

      <div className="where">
        <div><b>VALORANT</b><span>%LOCALAPPDATA%\VALORANT\Saved\Demos</span></div>
        <div><b>CS2</b><span>Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\replays</span></div>
        <div><b>Siege X</b><span>Documents\My Games\Rainbow Six - Siege\...\MatchReplay</span></div>
      </div>

      {state && (
        <ol className="steps" aria-live="polite">
          {STEPS.map((s, i) => (
            <li key={s.label} className={i < stepIndex || state === "done" ? "done" : i === stepIndex ? "now" : ""}>
              <span>{s.label}</span>
              {i === 0 && state === "uploading" && total > 0 && (
                <em>{Math.round((100 * sent) / total)}%</em>
              )}
            </li>
          ))}
        </ol>
      )}

      {error && <Problem title={state === "failed" ? "Analysis failed" : "Check the files"}>{error}</Problem>}

      <div className="actions">
        <button className="btn primary" disabled={!files.length || !!validate(files) || busy} onClick={start}>
          <span>{busy ? "Working" : "Analyze"}</span>
        </button>
        {files.length > 0 && !busy && <button className="btn" onClick={() => pick(null)}><span>Clear</span></button>}
      </div>
    </main>
  );
}
