/**
 * Health, readiness and metrics.
 *
 * `{"ok": true}` is not a health check -- it proves the process is running,
 * which the TCP connection already proved. These endpoints answer the two
 * questions an orchestrator actually asks, and they are different questions:
 *
 *   /api/health   liveness. Is this process wedged? A failure here means
 *                 restart me. It must never depend on anything external, or a
 *                 downstream outage turns into a restart loop.
 *
 *   /api/ready    readiness. Can I serve traffic *right now*? A failure means
 *                 take me out of the pool but do not kill me. Here that means
 *                 the artefacts the dashboard needs are actually readable --
 *                 the one failure mode that leaves every panel empty while the
 *                 process looks perfectly healthy.
 */

import { Router } from "express";

export function healthRouter({ artifacts, jobs, metrics, config, startedAt }) {
  const router = Router();

  router.get("/health", (_req, res) => {
    res.set("Cache-Control", "no-store");
    res.json({
      status: "ok",
      uptimeS: Number(((Date.now() - startedAt) / 1000).toFixed(1)),
      pid: process.pid,
      node: process.version,
    });
  });

  router.get("/ready", (_req, res) => {
    const checks = {};

    // The committed run is what the dashboard renders by default. If it cannot
    // be read, the UI is a shell and this instance should not take traffic.
    try {
      const entry = artifacts.get("reference");
      checks.referenceArtifact = {
        ok: Array.isArray(entry.data?.policies),
        bytes: entry.bytes,
      };
    } catch (err) {
      checks.referenceArtifact = { ok: false, error: err.message };
    }

    try {
      const entry = artifacts.get("sensitivity");
      checks.sensitivityArtifact = { ok: Array.isArray(entry.data?.points) };
    } catch (err) {
      // Degraded, not fatal: one card renders an empty state and the rest works.
      checks.sensitivityArtifact = { ok: false, error: err.message, required: false };
    }

    checks.job = { ok: true, running: jobs.status().running };

    const required = Object.entries(checks).filter(([, v]) => v.required !== false);
    const ready = required.every(([, v]) => v.ok);

    res.set("Cache-Control", "no-store");
    res.status(ready ? 200 : 503).json({ ready, checks });
  });

  router.get("/metrics", (_req, res) => {
    res.set("Cache-Control", "no-store");
    res.type("text/plain; version=0.0.4").send(metrics.render());
  });

  router.get("/info", (_req, res) => {
    res.json({
      service: "kavach-api",
      repo: config.repo,
      runsDir: config.runsDir,
      port: config.port,
      artifactCache: artifacts.stats(),
    });
  });

  return router;
}
