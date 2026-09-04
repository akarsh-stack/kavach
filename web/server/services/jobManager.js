/**
 * Owns the single evaluation run: its lifecycle, its subscribers, its history.
 *
 * The previous version was `let job = null` plus a Set of response objects.
 * That is enough to work and not enough to operate: there was no record of a
 * run once it ended, no timeout on a wedged child, no bound on the log buffer,
 * and no way to answer "did that fail, and why" after the page was closed.
 *
 * ## The state machine
 *
 *   idle ──start──▶ running ──┬─ exit 0 ────▶ succeeded
 *                             ├─ exit ≠ 0 ──▶ failed
 *                             ├─ timeout ───▶ timedOut
 *                             └─ cancel ────▶ cancelled
 *
 * Only one run at a time, deliberately. It writes `data/runs/latest.json` and
 * burns provider quota; two concurrent runs would race on the file and halve
 * each other's rate limit. Concurrency is refused with 409, not queued -- a
 * queue would let a judge click twice and wait twice as long for the same
 * answer.
 *
 * ## Why start and observe are different verbs
 *
 * Starting is POST. Observing is GET. This is not REST pedantry: `EventSource`
 * speaks only GET and reconnects on every network blip, so a GET with a spawn
 * behind it re-runs the job on reconnect. That bug shipped twice here. The
 * first fix guarded against *concurrent* runs and did not help, because a
 * reconnect after completion simply started a fresh one. Five evaluations were
 * spawned that nobody asked for. Keeping the side effect behind a verb
 * EventSource cannot speak is the only fix that actually holds.
 *
 * ## Idempotency
 *
 * A POST may carry `Idempotency-Key`. Replaying the same key while that run is
 * still active returns the same job instead of a 409, so a client that retries
 * on a dropped response does not get punished for it.
 */

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";

import { conflict } from "../lib/problem.js";

export const ENGINES = Object.freeze([
  "none",
  "ollama",
  "gemini",
  "groq",
  "anthropic",
  "stub",
  "replay",
]);

/**
 * Build the argv for run_eval.py from validated inputs.
 *
 * Whitelisted, never passed through. This spawns a process, so accepting
 * arbitrary flags would be a command-injection surface -- on a dashboard whose
 * subject is payment security that would be a poor look as well as a real bug.
 * Note there is no shell: `spawn` with an argv array cannot be argument-injected
 * even if a value slipped through.
 */
export function buildArgs({ repo, limit, seed, engine }) {
  const args = [
    path.join(repo, "scripts", "run_eval.py"),
    "--limit",
    String(limit),
    "--seed",
    String(seed),
    "--save",
    "latest",
  ];
  if (engine === "none") args.push("--no-llm");
  else if (engine === "stub") args.push("--stub");
  else args.push("--engine", engine, "--no-ablation");
  return args;
}

export function createJobManager({ config, logger, metrics, artifacts }) {
  /** @type {null | object} */
  let current = null;
  /** Terminal states, newest first. Bounded: this is an operator aid, not a database. */
  const history = [];
  const HISTORY_LIMIT = 20;

  const subscribers = new Set();

  const broadcast = (event, data) => {
    const frame = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const res of subscribers) {
      // Respect backpressure. If a client cannot keep up, Node buffers in
      // memory on our side; dropping a slow consumer is better than growing
      // the heap until the process dies.
      if (res.writableLength > 1 << 20) {
        logger.warn("dropping slow sse client", { buffered: res.writableLength });
        subscribers.delete(res);
        metrics.sseClients.dec();
        res.end();
        continue;
      }
      res.write(frame);
    }
  };

  const settle = (outcome, code) => {
    if (!current) return;
    clearTimeout(current.timer);
    const durationS = (Date.now() - current.startedAt) / 1000;

    const record = {
      id: current.id,
      outcome,
      code,
      args: current.args,
      startedAt: new Date(current.startedAt).toISOString(),
      durationS: Number(durationS.toFixed(2)),
      lines: current.lines.length,
    };
    history.unshift(record);
    if (history.length > HISTORY_LIMIT) history.pop();

    metrics.evaluationRuns.inc({ outcome });
    metrics.evaluationDuration.observe({ outcome }, durationS);
    logger.info("run finished", record);

    // The run rewrote latest.json; drop the cached parse so the next read is
    // the new file rather than the one the dashboard already had.
    artifacts.invalidate("latest");

    broadcast("done", { code, outcome, id: current.id });
    for (const res of subscribers) res.end();
    subscribers.clear();
    metrics.sseClients.set(0);
    current = null;
  };

  return {
    ENGINES,

    /** @returns {{id:string,args:string[],startedAt:string}} */
    start({ limit, seed, engine, idempotencyKey, requestId }) {
      if (current) {
        if (idempotencyKey && current.idempotencyKey === idempotencyKey) {
          logger.info("idempotent replay", { id: current.id, idempotencyKey });
          return { id: current.id, args: current.args, startedAt: new Date(current.startedAt).toISOString(), replayed: true };
        }
        throw conflict("a run is already in progress", {
          runningId: current.id,
          args: current.args,
        });
      }

      const args = buildArgs({ repo: config.repo, limit, seed, engine });
      const id = randomUUID();
      const proc = spawn(config.python, args, { cwd: config.repo });

      current = {
        id,
        proc,
        args: args.slice(1),
        lines: [],
        startedAt: Date.now(),
        idempotencyKey,
        timer: setTimeout(() => {
          logger.error("run exceeded timeout; killing", { id, ms: config.jobTimeoutMs });
          proc.kill("SIGKILL");
          settle("timedOut", -2);
        }, config.jobTimeoutMs),
      };

      logger.info("run started", { id, requestId, engine, limit, seed, args: current.args });

      let buffer = "";
      const pump = (chunk, stream) => {
        buffer += chunk.toString();
        const parts = buffer.split("\n");
        buffer = parts.pop() ?? "";
        const CR = String.fromCharCode(13);
        for (const raw of parts) {
          const line = raw.endsWith(CR) ? raw.slice(0, -1) : raw;
          current.lines.push(line);
          // Bounded ring buffer: a long run must not turn into an OOM just
          // because nobody is watching it.
          if (current.lines.length > config.jobLogLimit) current.lines.shift();
          broadcast("log", { stream, line });
        }
      };

      proc.stdout.on("data", (c) => pump(c, "out"));
      proc.stderr.on("data", (c) => pump(c, "err"));

      proc.on("close", (code) => {
        if (buffer) broadcast("log", { stream: "out", line: buffer });
        // A cancelled run exits non-zero, and without this flag `close` fires
        // first and records it as "failed". An operator reading the history
        // needs to tell a broken run from a deliberate shutdown; they call for
        // very different responses.
        if (current?.cancelling) return settle("cancelled", code);
        settle(code === 0 ? "succeeded" : "failed", code);
      });

      proc.on("error", (err) => {
        broadcast("log", { stream: "err", line: `failed to spawn python: ${err.message}` });
        logger.error("spawn failed", { id, err: err.message });
        settle("failed", -1);
      });

      return { id, args: current.args, startedAt: new Date(current.startedAt).toISOString() };
    },

    /** Attach an SSE response. Returns a detach function. */
    subscribe(res) {
      if (!current) return null;
      subscribers.add(res);
      metrics.sseClients.set(subscribers.size);
      res.write(`event: attached\ndata: ${JSON.stringify({ id: current.id, args: current.args })}\n\n`);
      // Replay the buffered log so a late subscriber sees the whole run, not
      // just the tail from the moment it connected.
      for (const line of current.lines) {
        res.write(`event: log\ndata: ${JSON.stringify({ stream: "out", line })}\n\n`);
      }
      return () => {
        subscribers.delete(res);
        metrics.sseClients.set(subscribers.size);
      };
    },

    status() {
      return {
        running: Boolean(current),
        id: current?.id ?? null,
        args: current?.args ?? null,
        startedAt: current ? new Date(current.startedAt).toISOString() : null,
        elapsedS: current ? Number(((Date.now() - current.startedAt) / 1000).toFixed(1)) : null,
        subscribers: subscribers.size,
        history,
      };
    },

    /** Used by graceful shutdown. Never leaves an orphaned Python process. */
    async cancel(reason = "shutdown") {
      if (!current) return false;
      logger.warn("cancelling run", { id: current.id, reason });
      current.cancelling = true;
      const proc = current.proc;
      proc.kill("SIGTERM");
      await new Promise((resolve) => {
        // SIGTERM is a request. A Python process wedged in a socket read will
        // ignore it, so there is a deadline and then SIGKILL -- otherwise
        // shutdown hangs on exactly the run that most needs killing.
        const t = setTimeout(() => {
          logger.warn("run ignored SIGTERM; sending SIGKILL", { id: current?.id });
          proc.kill("SIGKILL");
          resolve();
        }, 3000);
        proc.once("close", () => (clearTimeout(t), resolve()));
      });
      // `close` normally settles first; this only fires if the child was
      // already gone and no event is coming.
      settle("cancelled", -3);
      return true;
    },
  };
}
