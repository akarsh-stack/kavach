/**
 * Process entrypoint: configuration, wiring, listening, and dying properly.
 *
 * The app itself knows nothing about ports or signals -- see app.js. This file
 * is the only place that touches the process, which keeps every other module
 * testable without one of them deciding to call process.exit().
 *
 * ## The server owns no analysis
 *
 * It serves recorded JSON and shells out to the Python entrypoints. What the
 * dashboard shows is exactly what `scripts/run_eval.py` printed, and there is
 * no second implementation of the metrics to drift out of sync with the first.
 * If a number on screen looks wrong, it is wrong in the artefact, and the
 * artefact is in git.
 *
 * ## Shutdown
 *
 * A run is a spawned Python process writing a file. Exiting without cleaning up
 * leaves an orphan that finishes minutes later and overwrites `latest.json`
 * under whatever started next. So SIGTERM stops accepting connections, cancels
 * the child, closes open SSE streams, and only then exits -- with a hard
 * deadline, because a shutdown that hangs is just a different outage.
 */

import { createApp } from "./app.js";
import { loadConfig } from "./config.js";
import { createLogger } from "./lib/logger.js";
import { createMetrics } from "./lib/metrics.js";

let config;
try {
  config = loadConfig();
} catch (err) {
  // Configuration errors happen before a logger exists and must be readable.
  process.stderr.write(`\n  configuration error: ${err.message}\n\n`);
  process.exit(1);
}

const logger = createLogger({
  level: config.logLevel,
  pretty: config.logPretty,
  base: { service: "kavach-api", pid: process.pid },
});
const metrics = createMetrics();

const { app, jobs } = createApp({ config, logger, metrics });

const server = app.listen(config.port, config.host, () => {
  logger.info("listening", {
    url: `http://${config.host}:${config.port}`,
    repo: config.repo,
    runs: config.runsDir,
    dotEnvKeysLoaded: config.dotEnvKeysLoaded,
  });
});

/**
 * Without this, a taken port is an unhandled 'error' event: the API dies while
 * `concurrently` keeps the UI up, so the dashboard looks fine and every panel
 * quietly fails. Worse, if something ELSE is already serving this port, Vite
 * proxies to it happily and the client receives a stranger's JSON.
 *
 * Observed for real: an unrelated embeddings service was holding 5174 and the
 * dashboard rendered nothing with only a 404 in the console to go on.
 */
server.on("error", (err) => {
  if (err.code === "EADDRINUSE") {
    process.stderr.write(
      [
        "",
        `  Port ${config.port} is already in use.`,
        "  Something else is listening there — check with:",
        `    curl http://localhost:${config.port}/api/health`,
        "",
        "  If that answers with anything other than this API, either stop it",
        `  or run on another port:  API_PORT=5199 npm run dev`,
        "  The Vite proxy reads API_PORT too, so one variable moves both.",
        "",
      ].join("\n"),
    );
    process.exit(1);
  }
  logger.error("server error", { err: err.message, code: err.code });
  process.exit(1);
});

// Track sockets so shutdown can close idle keep-alives, which otherwise hold
// server.close() open for their full timeout.
const sockets = new Set();
server.on("connection", (s) => {
  sockets.add(s);
  s.on("close", () => sockets.delete(s));
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  logger.info("shutting down", { signal });

  const hard = setTimeout(() => {
    logger.error("graceful shutdown timed out; forcing exit");
    process.exit(1);
  }, config.shutdownGraceMs).unref();

  server.close(() => logger.debug("http server closed"));
  try {
    await jobs.cancel(signal);
  } catch (err) {
    logger.error("failed to cancel run", { err: err.message });
  }
  for (const s of sockets) s.destroy();

  clearTimeout(hard);
  logger.info("bye");
  process.exit(0);
}

for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => shutdown(sig));

// A crash mid-run would otherwise orphan the Python child.
process.on("uncaughtException", (err) => {
  logger.error("uncaught exception", { err: err.message, stack: err.stack });
  shutdown("uncaughtException");
});
process.on("unhandledRejection", (reason) => {
  logger.error("unhandled rejection", { reason: String(reason) });
});
