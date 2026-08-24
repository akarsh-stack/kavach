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

const app = express();
app.use(cors());
app.use(express.json());

const readRun = (name) => {
  const file = path.join(RUNS, `${name}.json`);
  if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, "utf-8"));

  // `latest` is a scratch artifact -- gitignored, and overwritten by any run
  // triggered from the dashboard. `reference` is the committed result the repo
  // ships. Falling back means a fresh clone shows real numbers immediately,
  // and a stub run started from the UI can never clobber the citable one.
  if (name === "latest") {
    const ref = path.join(RUNS, "reference.json");
    if (fs.existsSync(ref)) return JSON.parse(fs.readFileSync(ref, "utf-8"));
  }
  return null;
};

app.get("/api/health", (_req, res) => {
  res.json({ ok: true, repo: REPO, runsDir: RUNS });
});

app.get("/api/runs", (_req, res) => {
  if (!fs.existsSync(RUNS)) return res.json({ runs: [] });
  const runs = fs
    .readdirSync(RUNS)
    .filter((f) => f.endsWith(".json"))
    .map((f) => {
      const stem = f.replace(/\.json$/, "");
      const stat = fs.statSync(path.join(RUNS, f));
      return { name: stem, modified: stat.mtime.toISOString() };
    })
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

/**
 * One evaluation at a time, ever.
 *
 * This guard exists because of a bug we hit rather than one we anticipated: an
 * `EventSource` reconnects automatically when its connection drops, and every
 * reconnect re-issues the GET. With a process spawn behind that GET, restarting
 * the server mid-stream silently kicked off a fresh evaluation nobody asked
 * for -- which then overwrote data/runs/latest.json with different numbers.
 *
 * Any SSE endpoint with a side effect has this problem. A second caller now
 * attaches to the running job's output instead of starting a second one.
 */
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

app.get("/api/job", (_req, res) => {
  res.json({ running: Boolean(job), args: job?.args ?? null });
});

/**
 * Trigger an evaluation and stream stdout back as Server-Sent Events.
 *
 * Arguments are whitelisted rather than passed through. This endpoint spawns a
 * process, so accepting arbitrary flags from the client would be a command
 * injection surface -- on a dashboard whose entire subject is payment security,
 * that would be a poor look as well as a real bug.
 */
app.get("/api/evaluate", (req, res) => {
  // Attach to the in-flight run rather than starting a competing one.
  if (job) {
    sseHeaders(res);
    job.listeners.add(res);
    res.write(
      `event: attached\ndata: ${JSON.stringify({ args: job.args })}\n\n`
    );
    for (const line of job.lines) {
      res.write(`event: log\ndata: ${JSON.stringify({ stream: "out", line })}\n\n`);
    }
    req.on("close", () => job?.listeners.delete(res));
    return;
  }
  return startJob(req, res);
});

function startJob(req, res) {
  const limit = String(parseInt(req.query.limit, 10) || 300);
  const seed = String(parseInt(req.query.seed, 10) || 42);
  const engine = ["none", "ollama", "anthropic", "stub"].includes(req.query.engine)
    ? req.query.engine
    : "none";

  const args = [
    path.join(REPO, "scripts", "run_eval.py"),
    "--limit", limit,
    "--seed", seed,
    "--save", "latest",
  ];
  if (engine === "none") args.push("--no-llm");
  else if (engine === "stub") args.push("--stub");
  else args.push("--engine", engine);

  sseHeaders(res);

  const py = spawn(process.env.PYTHON || "python", args, { cwd: REPO });
  job = { proc: py, listeners: new Set([res]), lines: [], args: args.slice(1) };
  console.log(`[evaluate] spawn: ${args.slice(1).join(" ")}`);

  broadcast("start", { args: job.args });

  let buffer = "";
  const pump = (chunk, stream) => {
    buffer += chunk.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
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

  // A client hanging up must NOT kill the run. Killing it here was the other
  // half of the reconnect bug: the dropped connection aborted the evaluation
  // and the reconnect started a fresh one.
  req.on("close", () => job?.listeners.delete(res));
}

app.listen(PORT, () => {
  console.log(`recovery-agent api  http://localhost:${PORT}`);
  console.log(`  repo  ${REPO}`);
  console.log(`  runs  ${RUNS}`);
});
