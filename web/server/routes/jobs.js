/**
 * Starting, observing and inspecting an evaluation run.
 *
 * The POST/GET split here is load-bearing and must not be "tidied up" into one
 * endpoint. See services/jobManager.js for what happened the two times it was.
 */

import { Router } from "express";

import { badRequest } from "../lib/problem.js";
import { ENGINES } from "../services/jobManager.js";

/** Bounds chosen so a typo cannot start a run that costs hours of quota. */
const LIMITS = { limit: [1, 5000], seed: [0, 2 ** 31 - 1] };

/**
 * Validate at the boundary, once, and never trust a query string past here.
 *
 * The previous version coerced with `parseInt(x) || default`, which silently
 * turned `limit=abc` into 300 and `limit=-5` into -5. Quietly substituting a
 * different run than the one asked for is worse than refusing: the caller gets
 * results they did not request and no indication of it.
 */
function parseRunRequest(query) {
  const errors = [];

  const int = (name, fallback) => {
    const raw = query[name];
    if (raw === undefined || raw === "") return fallback;
    const n = Number(raw);
    const [lo, hi] = LIMITS[name];
    if (!Number.isInteger(n) || n < lo || n > hi) {
      errors.push(`${name} must be an integer in [${lo}, ${hi}]`);
      return fallback;
    }
    return n;
  };

  const limit = int("limit", 300);
  const seed = int("seed", 42);

  const engine = query.engine ?? "none";
  if (!ENGINES.includes(engine)) {
    errors.push(`engine must be one of: ${ENGINES.join(", ")}`);
  }

  if (errors.length) throw badRequest(errors.join("; "), { errors });
  return { limit, seed, engine };
}

export function jobsRouter({ jobs, config, rateLimiter }) {
  const router = Router();

  /** Start a run. POST, so EventSource can never replay it. */
  router.post("/evaluate", rateLimiter, (req, res) => {
    const { limit, seed, engine } = parseRunRequest(req.query);
    const key = req.get("idempotency-key") || undefined;
    const job = jobs.start({ limit, seed, engine, idempotencyKey: key, requestId: req.id });
    res.status(job.replayed ? 200 : 202).json({ started: true, ...job });
  });

  /**
   * Observe the current run. No side effect; safe to reconnect at will.
   *
   * Heartbeats are comment frames (`:`), which SSE ignores. Without them an
   * idle stream dies silently to any proxy with an idle timeout, and the client
   * reconnects in a loop that looks like a server bug.
   */
  router.get("/stream", (req, res) => {
    res.set({
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // Nginx buffers event-streams by default and the UI just hangs.
      "X-Accel-Buffering": "no",
    });
    res.flushHeaders();

    const detach = jobs.subscribe(res);
    if (!detach) {
      res.write("event: idle\ndata: {}\n\n");
      return res.end();
    }

    const heartbeat = setInterval(() => res.write(": keep-alive\n\n"), config.sseHeartbeatMs);
    // A client hanging up must never kill the run.
    req.on("close", () => {
      clearInterval(heartbeat);
      detach();
    });
  });

  router.get("/job", (req, res) => {
    res.set("Cache-Control", "no-store");
    res.json(jobs.status());
  });

  return router;
}
