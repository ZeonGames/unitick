import { Link } from "react-router-dom";
import type { MatchSummary, TeamSummary } from "../api";

const FALLBACK: TeamSummary[] = [
  { name: "Team A", score: 0, start_side: "attack" },
  { name: "Team B", score: 0, start_side: "defense" },
];

function sideNote(t: TeamSummary) {
  return t.start_side === "defense" ? "started on defense" : "started on attack";
}

/** One match as a scoreboard strip: team, score and map, team. */
export default function MatchRow({ m, onDelete }: { m: MatchSummary; onDelete?: (id: string) => void }) {
  const [a, b] = m.teams.length === 2 ? m.teams : FALLBACK;
  const left = m.mode ? `${m.mode}, ${sideNote(a)}` : sideNote(a).replace(/^s/, "S");
  const roster = b.players?.length ? b.players.join(", ") : "";
  const right = roster || `${m.rounds} rounds, ${m.kills} deaths analyzed`;
  const under = m.patch ? `Patch ${m.patch}` : m.event;
  return (
    <div className="mrow">
      <Link to={`/match/${m.id}`} className="m">
        <span className="t l">{a.name}<small>{left}</small></span>
        <span className="s">
          <b className={a.score > b.score ? "w" : ""}>{a.score}</b>
          <span>{m.map_display}<small>{under}</small></span>
          <b className={b.score > a.score ? "w" : ""}>{b.score}</b>
        </span>
        <span className="t r">{b.name}<small>{right}</small></span>
      </Link>
      {onDelete && (
        <button className="del" onClick={() => onDelete(m.id)} aria-label={`Remove ${a.name} vs ${b.name} on ${m.map_display}`}>
          Remove
        </button>
      )}
    </div>
  );
}
