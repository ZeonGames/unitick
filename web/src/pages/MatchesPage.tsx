import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Game, type MatchSummary } from "../api";
import MatchRow from "../components/MatchRow";
import { Problem, Spinner } from "../components/Status";

const SECTIONS: { game: Game; title: string }[] = [
  { game: "valorant", title: "VALORANT" },
  { game: "cs2", title: "Counter-Strike 2" },
  { game: "r6", title: "Rainbow Six Siege X" },
];

const FEATURES: [string, string][] = [
  ["Why you died", "Every death labeled: looking away, caught in a crossfire, never fired back, or lost a fair duel. Line of sight is checked against the map's walls."],
  ["What if", "Your odds of surviving if you had held, pushed or fallen back, from the closest pro moments, with how many players each number rests on."],
  ["What pros did here", "The closest situations from pro demos, drawn on the map, with how many held, pushed or fell back and how many survived."],
  ["Map control", "Which ground each team reaches first and what each can see, live on the replay. Smokes block sight, fires block movement."],
  ["Crosshair placement", "How far your crosshair was from the enemy's head the moment they appeared, split into sideways and height."],
  ["Fights", "Kills, damage per round, KAST, opening duels and trades for every player, checked against independent parsers."],
  ["Executes", "Every site take drawn as a play: paths, grenades, entry fights, and whether it won the round."],
  ["Bullets in 3D", "Every shot as a beam from the shooter's eye: misses stop at the wall they hit, hits land on the player, kills light up."],
  ["Video, synced", "Click a kill and your NVIDIA or OBS recording jumps to it, with the 2D map beside it."],
];

function valorantLead(list: MatchSummary[]) {
  const comp = list.filter((m) => m.mode === "Competitive").length;
  const swift = list.filter((m) => m.mode === "Swiftplay").length;
  const maps = new Set(list.map((m) => m.map_display)).size;
  const parts = [comp && `${comp === 1 ? "One" : comp} competitive match${comp === 1 ? "" : "es"}`,
                 swift && `${swift} Swiftplay match${swift === 1 ? "" : "es"}`].filter(Boolean);
  if (!parts.length) return null;
  return `${parts.join(" and ")} across ${maps} map${maps === 1 ? "" : "s"}. Players are shown by agent.`;
}

export default function MatchesPage() {
  const [rows, setRows] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.matches().then((r) => live && setRows(r)).catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, []);

  async function remove(id: string) {
    try {
      await api.remove(id);
      setRows((r) => (r ?? []).filter((m) => m.id !== id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const mine = (rows ?? []).filter((m) => m.kind !== "sample");
  const samples = (rows ?? []).filter((m) => m.kind === "sample");

  return (
    <main className="home">
      <h1>Why you died, and your odds with every other play.</h1>
      <p className="lead">
        VALORANT, CS2 and Rainbow Six Siege save a replay of every match to your PC. unitick reads that file offline
        and rebuilds every death: how long the killer could see you, where your crosshair was, whether you shot back,
        who else had you in sight. In CS2 it then finds the moments closest to yours in 82 professional matches and
        gives you your odds of surviving if you had held, pushed or fallen back. Open any match below and pick the
        Deaths tab.
      </p>

      {error && <Problem title="Could not load matches">{error}</Problem>}
      {!error && rows === null && <Spinner label="Reading the matches on this PC" />}

      {rows && (
        <section className="game">
          <h2>Your matches</h2>
          {mine.length ? (
            <div className="matches">
              {mine.map((m) => <MatchRow key={m.id} m={m} onDelete={m.kind === "upload" ? remove : undefined} />)}
            </div>
          ) : (
            <div className="empty">
              <p>Drop a replay from your last match and its report shows up here. Replays never leave this PC.</p>
              <Link to="/upload" className="btn primary"><span>Analyze a replay</span></Link>
            </div>
          )}
        </section>
      )}

      {SECTIONS.map(({ game, title }) => {
        // Competitive before Swiftplay, then the newest patch first; other games keep the server's order.
        const list = samples.filter((m) => m.game === game).sort((x, y) =>
          Number(y.mode === "Competitive") - Number(x.mode === "Competitive") ||
          (y.patch ?? "").localeCompare(x.patch ?? "", undefined, { numeric: true }));
        if (!list.length) return null;
        const lead = game === "valorant" ? valorantLead(list) : null;
        return (
          <section key={game} className="game">
            <h2>{title}</h2>
            {lead && <p className="lead sub">{lead}</p>}
            <div className="matches">{list.map((m) => <MatchRow key={m.id} m={m} />)}</div>
          </section>
        );
      })}

      <h2>What you get</h2>
      <div className="cols">
        {FEATURES.map(([t, d]) => <div key={t}><b>{t}</b><span>{d}</span></div>)}
      </div>

      <h2>Supported games</h2>
      <table className="games">
        <thead><tr><th>Game</th><th>Replay file</th><th>What unitick reads</th></tr></thead>
        <tbody>
          <tr><td>VALORANT</td><td><code>.vrf</code>, saved after every match</td><td>Position and aim of all ten players 128 times a second, kills, spike and abilities</td></tr>
          <tr><td>CS2</td><td><code>.dem</code></td><td>Every position, aim angle, grenade and kill, plus the map's walls</td></tr>
          <tr><td>Siege X</td><td><code>.rec</code>, one per round</td><td>Kill feed, plants, rosters and round results</td></tr>
        </tbody>
      </table>

      <footer className="foot">unitick is open source and built by <a href="https://zeonlabs.co">Zeon Labs</a>.</footer>
    </main>
  );
}
