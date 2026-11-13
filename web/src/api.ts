export type Game = "valorant" | "cs2" | "r6";

export interface TeamSummary {
  name: string;
  score: number;
  start_side: "attack" | "defense" | null;
  players?: string[];
}

export interface MatchSummary {
  id: string;
  kind: "sample" | "watch" | "upload";
  game: Game;
  map: string;
  map_display: string;
  rounds: number;
  kills: number;
  event: string;
  teams: TeamSummary[];
  mode?: string | null;
  patch?: string | null;
  added: number;
}

export type JobState = "uploading" | "queued" | "parsing" | "analyzing" | "done" | "failed";

export interface Job {
  id: string;
  state: JobState;
  files: string[];
  error: string | null;
  match_id: string | null;
}

export const GAME_LABEL: Record<Game, string> = { valorant: "VALORANT", cs2: "CS2", r6: "Siege X" };

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("The unitick API is not running. Start it with: unitick app", 0);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError("The unitick API is not running. Start it with: unitick app", res.status);
  }
  if (!res.ok) {
    const msg = (body as { error?: string } | null)?.error ?? `Request failed (${res.status})`;
    throw new ApiError(msg, res.status);
  }
  return body as T;
}

export const api = {
  matches: () => call<MatchSummary[]>("/api/matches"),
  match: (id: string) => call<unknown>(`/api/matches/${encodeURIComponent(id)}`),
  remove: (id: string) => call<{ deleted: boolean }>(`/api/matches/${encodeURIComponent(id)}`, { method: "DELETE" }),
  createJob: () => call<Job>("/api/jobs", { method: "POST" }),
  runJob: (id: string) => call<Job>(`/api/jobs/${id}/run`, { method: "POST" }),
  job: (id: string) => call<Job>(`/api/jobs/${id}`),
  /** Upload one file with progress (fetch cannot report upload progress). */
  uploadFile(jobId: string, file: File, onProgress: (sent: number) => void): Promise<Job> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `/api/jobs/${jobId}/files/${encodeURIComponent(file.name)}`);
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        let body: { error?: string } & Partial<Job> = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* handled below */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as Job);
        else reject(new ApiError(body.error ?? `Upload failed (${xhr.status})`, xhr.status));
      };
      xhr.onerror = () => reject(new ApiError("Upload interrupted. Is unitick app still running?", 0));
      xhr.send(file);
    });
  },
};
