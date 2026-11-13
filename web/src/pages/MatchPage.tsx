import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { mountReport } from "@render/report.js";
import { api } from "../api";
import { Problem, Spinner } from "../components/Status";

export default function MatchPage() {
  const { id = "" } = useParams();
  const host = useRef<HTMLDivElement>(null);
  const [payload, setPayload] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    let live = true;
    setPayload(null);
    setError(null);
    setElapsed(0);
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    api.match(id)
      .then((p) => live && setPayload(p))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => window.clearInterval(timer));
    return () => { live = false; window.clearInterval(timer); };
  }, [id]);

  useEffect(() => {
    if (!payload || !host.current) return;
    const unmount = mountReport(host.current, payload, { embedded: true });
    window.scrollTo(0, 0);
    return unmount;
  }, [payload]);

  return (
    <main className="matchpage">
      <div className="crumbs"><Link to="/">‹ All matches</Link></div>
      {error && <div className="page"><Problem title="This report could not be built">{error}</Problem></div>}
      {!error && !payload && (
        <div className="page">
          <Spinner label={elapsed < 3 ? "Opening the report" : `Building the report, ${elapsed} s. The first open of a match takes up to a minute; after that it is instant.`} />
        </div>
      )}
      <div ref={host} />
    </main>
  );
}
