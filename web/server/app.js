/**
 * Assembles the HTTP app from its parts.
 *
 * Exported separately from the process entrypoint so tests can drive a real
 * app over a real socket without spawning a server, and without the module
 * having decided anything about ports, signals or process exit. Everything the
 * app needs arrives as an argument -- that is what makes it testable, and it is
 * why there are no module-level singletons anywhere in here.
 *
 * Middleware order is deliberate and fragile:
 *
 *   1. request context   so everything below it can log with a request id
 *   2. cors / json       cheap, and needed before any handler
 *   3. routes            health first: it must answer even if a route below
 *                        throws at construction
 *   4. 404               anything unmatched becomes a Problem
 *   5. error handler     the single place a failure becomes a response
 */

import fs from "node:fs";

import cors from "cors";
import express from "express";

import { createArtifactStore } from "./services/artifactStore.js";
import { createJobManager } from "./services/jobManager.js";
import { errorHandler, notFoundHandler } from "./middleware/errorHandler.js";
import { rateLimit } from "./middleware/rateLimit.js";
import { rememberMount, requestContext } from "./middleware/requestContext.js";
import { healthRouter } from "./routes/health.js";
import { jobsRouter } from "./routes/jobs.js";
import { runsRouter } from "./routes/runs.js";

/**
 * What this machine can actually do right now, given its credentials.
 *
 * The UI used to offer every engine unconditionally. Picking one without a key
 * fell through to replay, and replay only covers the recorded batch size -- so
 * choosing "Groq" at 300 payments produced a Python traceback in the log pane.
 * A judge clicking a dropdown must not be able to reach that.
 *
 * The decision cache is stat-cached rather than reparsed: it is 700 KB and
 * this endpoint is hit on every page load.
 */
function makeCapabilities({ config, logger }) {
  let cached = { entries: 0, model: null, limit: null };
  let seen = { mtimeMs: -1, size: -1 };

  const readCache = () => {
    let stat;
    try {
      stat = fs.statSync(config.cacheFile);
    } catch {
      return cached; // no cache is a valid state
    }
    if (stat.mtimeMs === seen.mtimeMs && stat.size === seen.size) return cached;
    try {
      const parsed = JSON.parse(fs.readFileSync(config.cacheFile, "utf-8"));
      const entries = Object.values(parsed.entries || {});
      const counts = {};
      for (const e of entries) counts[e.model] = (counts[e.model] || 0) + 1;
      const model = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0] || null;
      cached = { entries: entries.length, model, limit: 150 };
      seen = { mtimeMs: stat.mtimeMs, size: stat.size };
    } catch (err) {
      logger.warn("decision cache unreadable", { err: err.message });
    }
    return cached;
  };

  return () => {
    const has = (k) => Boolean(process.env[k] && process.env[k].trim());
    const cache = readCache();

    return {
      engines: [
        { id: "none", label: "No model · 3 baselines", available: true },
        { id: "ollama", label: "Ollama", available: has("OLLAMA_API_KEY"), why: "needs OLLAMA_API_KEY" },
        { id: "gemini", label: "Gemini", available: has("GEMINI_API_KEY"), why: "needs GEMINI_API_KEY (free)" },
        { id: "groq", label: "Groq", available: has("GROQ_API_KEY"), why: "needs GROQ_API_KEY (free)" },
        { id: "anthropic", label: "Anthropic", available: has("ANTHROPIC_API_KEY"), why: "needs ANTHROPIC_API_KEY" },
        { id: "replay", label: "Replay recorded run", available: cache.entries > 0 },
      ],
      cache,
    };
  };
}

export function createApp({ config, logger, metrics }) {
  const startedAt = Date.now();

  const artifacts = createArtifactStore({ runsDir: config.runsDir, metrics, logger });
  const jobs = createJobManager({ config, logger, metrics, artifacts });

  const app = express();
  // Behind Vite's proxy in dev and possibly a real one later; without this,
  // req.ip is the proxy for every caller and the rate limiter becomes global.
  app.set("trust proxy", true);
  app.disable("x-powered-by");

  app.use(requestContext({ logger, metrics }));
  app.use(cors());
  app.use(express.json({ limit: "64kb" }));

  app.use("/api", rememberMount, healthRouter({ artifacts, jobs, metrics, config, startedAt }));
  app.use("/api", rememberMount, runsRouter({ artifacts, capabilities: makeCapabilities({ config, logger }) }));
  // The limiter is attached to the route rather than mounted at a path of its
  // own. Mounting it separately rewrote req.baseUrl for that leg, so the same
  // endpoint reported two different route labels in metrics depending on
  // whether the limiter or the router answered.
  app.use(
    "/api",
    rememberMount,
    jobsRouter({ jobs, config, rateLimiter: rateLimit({ ...config.rateLimit, metrics }) }),
  );

  app.use(notFoundHandler());
  app.use(errorHandler());

  return { app, artifacts, jobs, startedAt };
}
