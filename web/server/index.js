/**
 * Reads the artefacts the Python pipeline writes; can also trigger a run.
 *
 * The server deliberately owns no analysis. It serves recorded JSON and shells
 * out to the Python entrypoints -- so what the dashboard shows is exactly what
 * `scripts/run_eval.py` printed, and there is no second implementation of the
 * metrics to drift out of sync with the first. If a number on screen looks
 * wrong, it is wrong in the artefact, and the artefact is in git.
 */

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import cors from "cors";
import express from "express";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const RUNS = path.join(REPO, "data", "runs");

// Deliberately API_PORT, not PORT. Dev harnesses commonly inject PORT for the
// UI process, and inheriting it here makes the API silently squat on the
// frontend's port -- which fails as a confusing "Cannot GET /" rather than as
// an address-in-use error.
const PORT = process.env.API_PORT || 5174;

// Load .env ourselves. The server is started by npm and does not inherit a
// shell that sourced it, so without this every provider reports 'no key' and
// the UI disables engines that actually work. No dependency needed.
//
// Parsed without regex on purpose: this file has been mangled twice by
// escape handling in tooling, and indexOf cannot be mis-escaped.
function loadDotEnv() {
  let text;
  try {
    text = fs.readFileSync(path.join(REPO, '.env'), 'utf-8');
  } catch {
    return; // no .env is a perfectly valid state
  }
  const NL = String.fromCharCode(10);
  for (const raw of text.split(NL)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    // Never override something already in the environment.
    if (key && !process.env[key]) process.env[key] = value;
  }
}
loadDotEnv();

const app = express();
app.use(cors());
app.use(express.json());

const readRun = (name) => {
  const file = path.join(RUNS, `${name}.json`);
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, "utf-8"));

  // `latest` is a scratch artefact -- gitignored, and overwritten by any run
  // triggered from the dashboard. `reference` is the committed result the repo
  // ships. Falling back means a fresh clone shows real numbers immediately,
  // and a scratch run started from the UI can never clobber the citable one.
  if (name === "latest") {
    const ref = path.join(RUNS, "reference.json");
    if (fs.existsSync(ref)) return JSON.parse(fs.readFileSync(ref, "utf-8"));
  }
  return null;
};

/**
 * What this machine can actually do right now.
 *
 * The UI used to offer every engine unconditionally. Picking one without a key
 * fell through to replay, and replay only covers the recorded batch size -- so
 * choosing "Groq" at 300 payments produced a Python traceback in the log pane.
 * A judge clicking a dropdown must not be able to reach that.
 *
 * The server knows which keys exist and what the cache covers, so it says so
 * and the UI disables the rest.
 */
app.get("/api/capabilities", (_req, res) => {
  const has = (k) => Boolean(process.env[k] && process.env[k].trim());

  let cached = { entries: 0, model: null, limit: null };
  try {
    const raw = JSON.parse(
      fs.readFileSync(path.join(REPO, "data", "llm_cache.json"), "utf-8"),
    );
    const entries = Object.values(raw.entries || {});
    const counts = {};
    for (const e of entries) counts[e.model] = (counts[e.model] || 0) + 1;
    const model = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || null;
    cached = { entries: entries.length, model, limit: 150 };
  } catch {
    /* no cache is a valid state */
  }

  res.json({
    engines: [
      { id: "none", label: "No model · 3 baselines", available: true },
      {
        id: "ollama",
        label: "Ollama",
        available: has("OLLAMA_API_KEY"),
        why: "needs OLLAMA_API_KEY",
      },
      {
        id: "gemini",
        label: "Gemini",
        available: has("GEMINI_API_KEY"),
        why: "needs GEMINI_API_KEY (free)",
      },
      {
        id: "groq",
        label: "Groq",
        available: has("GROQ_API_KEY"),
        why: "needs GROQ_API_KEY (free)",
      },
      {
        id: "anthropic",
        label: "Anthropic",
        available: has("ANTHROPIC_API_KEY"),
        why: "needs ANTHROPIC_API_KEY",
      },
      { id: "replay", label: "Replay recorded run", available: cached.entries > 0 },
    ],
    cache: cached,
  });
});

app.get("/api/health", (_req, res) => {
  res.json({ ok: true, repo: REPO, runsDir: RUNS });
});

app.get("/api/runs", (_req, res) => {
  if (!fs.existsSync(RUNS)) return res.json({ runs: [] });
  const runs = fs
    .readdirSync(RUNS)
    .filter((f) => f.endsWith(".json"))
    .map((f) => ({
      name: f.replace(/\.json$/, ""),
      modified: fs.statSync(path.join(RUNS, f)).mtime.toISOString(),
    }))
    .sort((a, b) => b.modified.localeCompare(a.modified));
  res.json({ runs });
});

app.get("/api/run/:name", (req, res) => {
  const data = readRun(req.params.name);
  if (!data) return res.status(404).json({ error: `no run '${req.params.name}'` });
  res.json(data);
});

app.get("/api/sensitivity", (_req, res) => {
  const data = readRun("sensitivity");
  if (!data) return res.status(404).json({ error: "no sensitivity artefact yet" });
  res.json(data);
});

/* ===========================================================================
   Running an evaluation
   ===========================================================================

   Starting a run is a POST. Watching one is a GET. They are separate
   endpoints, and that separation is the entire point.

   `EventSource` speaks only GET, and it reconnects automatically whenever the
   stream drops -- so a GET that spawns a process gets re-run on every blip. We
   hit this twice. The first fix guarded against *concurrent* runs, which was
   not enough: a reconnect after a run had finished simply started a fresh one,
   and the dashboard silently spawned five evaluations nobody asked for,
   overwriting the results file each time.

   Now the side effect is not reachable by the method EventSource speaks. A
   reconnect re-attaches to the stream and finds either a running job or
   nothing at all.
   ========================================================================= */

let job = null; // { proc, listeners:Set<res>, lines:string[], args }

const broadcast = (event, data) => {
  if (!job) return;
  const frame = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const r of job.listeners) r.write(frame);
};

const sseHeaders = (res) => {
  res.set({
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });
  res.flushHeaders();
};

/**
 * Arguments are whitelisted, never passed through. This spawns a process, so
 * accepting arbitrary flags would be a command-injection surface -- on a
 * dashboard whose subject is payment security, that would be a poor look as
 * well as a real bug.
 */
function buildArgs(query) {
  const limit = String(parseInt(query.limit, 10) || 300);
  const seed = String(parseInt(query.seed, 10) || 42);
  const allowed = ["none", "ollama", "gemini", "groq", "anthropic", "stub", "replay"];
  const engine = allowed.includes(query.engine) ? query.engine : "none";

  const args = [
    path.join(REPO, "scripts", "run_eval.py"),
    "--limit", limit,
    "--seed", seed,
    "--save", "latest",
  ];
  if (engine === "none") args.push("--no-llm");
  else if (engine === "stub") args.push("--stub");
  else args.push("--engine", engine, "--no-ablation");
  return args;
}

function startJob(args) {
  const py = spawn(process.env.PYTHON || "python", args, { cwd: REPO });
  job = { proc: py, listeners: new Set(), lines: [], args: args.slice(1) };
  console.log(`[evaluate] spawn: ${args.slice(1).join(" ")}`);

  let buffer = "";
  const pump = (chunk, stream) => {
    buffer += chunk.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    const CR = String.fromCharCode(13);
    for (const rawLine of lines) {
      const line = rawLine.endsWith(CR) ? rawLine.slice(0, -1) : rawLine;
      job.lines.push(line);
      broadcast("log", { stream, line });
    }
  };

  py.stdout.on("data", (c) => pump(c, "out"));
  py.stderr.on("data", (c) => pump(c, "err"));

  const finish = (code) => {
    if (buffer) broadcast("log", { stream: "out", line: buffer });
    broadcast("done", { code });
    for (const r of job.listeners) r.end();
    console.log(`[evaluate] exit ${code}`);
    job = null;
  };

  py.on("close", finish);
  py.on("error", (err) => {
    broadcast("log", { stream: "err", line: `failed to spawn python: ${err.message}` });
    finish(-1);
  });
}

/** Start a run. POST, so EventSource can never replay it. */
app.post("/api/evaluate", (req, res) => {
  if (job) {
    return res.status(409).json({ error: "a run is already in progress", args: job.args });
  }
  startJob(buildArgs(req.query));
  res.json({ started: true, args: job.args });
});

/** Observe the current run. No side effect; safe to reconnect at will. */
app.get("/api/stream", (req, res) => {
  sseHeaders(res);
  if (!job) {
    res.write("event: idle\ndata: {}\n\n");
    res.end();
    return;
  }
  job.listeners.add(res);
  res.write(`event: attached\ndata: ${JSON.stringify({ args: job.args })}\n\n`);
  for (const line of job.lines) {
    res.write(`event: log\ndata: ${JSON.stringify({ stream: "out", line })}\n\n`);
  }
  // A client hanging up must not kill the run.
  req.on("close", () => job?.listeners.delete(res));
});

app.get("/api/job", (_req, res) => {
  res.json({ running: Boolean(job), args: job?.args ?? null });
});

app.listen(PORT, () => {
  console.log(`recovery-agent api  http://localhost:${PORT}`);
  console.log(`  repo  ${REPO}`);
  console.log(`  runs  ${RUNS}`);
});
