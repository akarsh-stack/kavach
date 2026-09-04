/**
 * Configuration, resolved and validated once at startup.
 *
 * Everything the process needs is read here and nowhere else, so there is a
 * single place to look when a deployment behaves differently from a laptop.
 * Invalid configuration is a startup failure, not a surprise on the first
 * request that happens to touch it -- a server that boots and then 500s on
 * one endpoint is much harder to diagnose than one that refuses to boot.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..");

/**
 * Load `.env` without a regex.
 *
 * The server is started by npm and does not inherit a shell that sourced the
 * file, so without this every provider reports "no key" and the UI disables
 * engines that actually work.
 *
 * Parsed with indexOf on purpose. This file has been mangled twice by escape
 * handling in tooling, and indexOf cannot be mis-escaped.
 */
function loadDotEnv(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf-8");
  } catch {
    return 0; // absent is a valid state: the cache replays without keys
  }
  const NL = String.fromCharCode(10);
  let loaded = 0;
  for (const raw of text.split(NL)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    // Never override the real environment. A container's injected secret must
    // win over a stale file that happened to get copied into the image.
    if (key && !process.env[key]) {
      process.env[key] = value;
      loaded += 1;
    }
  }
  return loaded;
}

const asInt = (raw, fallback, { min, max, name }) => {
  if (raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new Error(`${name} must be an integer in [${min}, ${max}], got ${JSON.stringify(raw)}`);
  }
  return n;
};

const asBool = (raw, fallback) => {
  if (raw === undefined || raw === "") return fallback;
  return ["1", "true", "yes", "on"].includes(String(raw).toLowerCase());
};

export function loadConfig(env = process.env) {
  const dotEnvCount = loadDotEnv(path.join(REPO, ".env"));

  const config = {
    repo: REPO,
    runsDir: path.join(REPO, "data", "runs"),
    cacheFile: path.join(REPO, "data", "llm_cache.json"),

    // Deliberately API_PORT, not PORT. Dev harnesses commonly inject PORT for
    // the UI process; inheriting it here makes the API squat on the frontend's
    // port, which fails as a confusing "Cannot GET /" rather than EADDRINUSE.
    port: asInt(env.API_PORT, 5185, { min: 1, max: 65535, name: "API_PORT" }),
    host: env.API_HOST || "127.0.0.1",

    python: env.PYTHON || "python",

    // A run spawns a Python process that can take minutes. This bounds it so a
    // wedged child cannot hold the single job slot forever.
    jobTimeoutMs: asInt(env.JOB_TIMEOUT_MS, 45 * 60_000, {
      min: 10_000,
      max: 4 * 60 * 60_000,
      name: "JOB_TIMEOUT_MS",
    }),

    // Ring buffer of log lines kept for late SSE subscribers. Unbounded growth
    // here is how a long run turns into an OOM.
    jobLogLimit: asInt(env.JOB_LOG_LIMIT, 5000, {
      min: 100,
      max: 200_000,
      name: "JOB_LOG_LIMIT",
    }),

    // Token bucket on the one endpoint that spawns a process.
    rateLimit: {
      capacity: asInt(env.RATE_LIMIT_BURST, 5, { min: 1, max: 1000, name: "RATE_LIMIT_BURST" }),
      refillPerMinute: asInt(env.RATE_LIMIT_RPM, 10, {
        min: 1,
        max: 10_000,
        name: "RATE_LIMIT_RPM",
      }),
    },

    sseHeartbeatMs: asInt(env.SSE_HEARTBEAT_MS, 15_000, {
      min: 1000,
      max: 300_000,
      name: "SSE_HEARTBEAT_MS",
    }),

    shutdownGraceMs: asInt(env.SHUTDOWN_GRACE_MS, 10_000, {
      min: 0,
      max: 120_000,
      name: "SHUTDOWN_GRACE_MS",
    }),

    logLevel: (env.LOG_LEVEL || "info").toLowerCase(),
    logPretty: asBool(env.LOG_PRETTY, process.stdout.isTTY === true),

    dotEnvKeysLoaded: dotEnvCount,
  };

  if (!fs.existsSync(config.repo)) {
    throw new Error(`repo root does not exist: ${config.repo}`);
  }
  if (!["debug", "info", "warn", "error"].includes(config.logLevel)) {
    throw new Error(`LOG_LEVEL must be one of debug|info|warn|error, got "${config.logLevel}"`);
  }
  return config;
}
