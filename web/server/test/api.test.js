/**
 * Server tests, driven over a real socket with the built-in runner.
 *
 * The server previously had none, which is why several of the behaviours these
 * assert were broken at some point without anyone noticing: the artefact
 * fallback, the concurrency guard, the argument whitelist.
 *
 * Deliberately no supertest and no mocking framework. `node --test` and
 * `fetch` are in the runtime, the app already takes all its dependencies as
 * arguments, and a real listener over a real port is a more honest test than a
 * mocked one -- these are the paths a browser actually takes.
 *
 * Run with:  npm --prefix web/server test
 */

import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";

import { createApp } from "../app.js";
import { loadConfig } from "../config.js";
import { createLogger } from "../lib/logger.js";
import { createMetrics } from "../lib/metrics.js";
import { buildArgs } from "../services/jobManager.js";

let server;
let base;
let ctx;

before(async () => {
  const config = { ...loadConfig(), logLevel: "error" };
  const logger = createLogger({ level: "error", pretty: false, sink: { write() {} } });
  ctx = createApp({ config, logger, metrics: createMetrics() });
  await new Promise((resolve) => {
    server = ctx.app.listen(0, "127.0.0.1", resolve);
  });
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await ctx.jobs.cancel("test-teardown");
  await new Promise((r) => server.close(r));
});

const get = (p, init) => fetch(`${base}${p}`, init);

describe("health and readiness", () => {
  it("liveness never depends on anything external", async () => {
    const r = await get("/api/health");
    assert.equal(r.status, 200);
    const body = await r.json();
    assert.equal(body.status, "ok");
    assert.ok(typeof body.uptimeS === "number");
  });

  it("readiness actually checks the artefacts the dashboard needs", async () => {
    const r = await get("/api/ready");
    const body = await r.json();
    assert.equal(r.status, 200, JSON.stringify(body));
    assert.equal(body.ready, true);
    assert.equal(body.checks.referenceArtifact.ok, true);
    assert.ok(body.checks.referenceArtifact.bytes > 1000);
  });

  it("exposes prometheus metrics after traffic", async () => {
    await get("/api/health");
    const r = await get("/api/metrics");
    const text = await r.text();
    assert.match(r.headers.get("content-type"), /text\/plain/);
    assert.match(text, /http_requests_total/);
    assert.match(text, /http_request_duration_seconds_bucket/);
  });
});

describe("request correlation", () => {
  it("assigns a request id and echoes it", async () => {
    const r = await get("/api/health");
    assert.match(r.headers.get("x-request-id"), /^[0-9a-f-]{36}$/i);
  });

  it("honours a sane inbound id so a trace survives a hop", async () => {
    const r = await get("/api/health", { headers: { "X-Request-Id": "abc-123" } });
    assert.equal(r.headers.get("x-request-id"), "abc-123");
  });

  it("rejects a hostile inbound id rather than reflecting it", async () => {
    const r = await get("/api/health", { headers: { "X-Request-Id": "a".repeat(500) } });
    assert.notEqual(r.headers.get("x-request-id"), "a".repeat(500));
  });
});

describe("run artefacts", () => {
  it("serves the committed run", async () => {
    const r = await get("/api/run/reference");
    assert.equal(r.status, 200);
    const body = await r.json();
    assert.ok(Array.isArray(body.policies));
    assert.ok(body.policies.some((p) => p.policy === "agent"));
  });

  it("returns a strong ETag and then a 304 with no body", async () => {
    const first = await get("/api/run/reference");
    const etag = first.headers.get("etag");
    assert.ok(etag, "expected an ETag");

    const second = await get("/api/run/reference", { headers: { "If-None-Match": etag } });
    assert.equal(second.status, 304);
    assert.equal((await second.text()).length, 0);
  });

  it("falls back to the committed run when latest is absent", async () => {
    // `latest` is scratch and gitignored, so a fresh clone has none. Falling
    // back means a first-time visitor sees real numbers rather than an error.
    const r = await get("/api/run/latest");
    assert.equal(r.status, 200);
    assert.ok(Array.isArray((await r.json()).policies));
  });

  it("refuses a traversal attempt as a 404, not a read", async () => {
    const r = await get("/api/run/..%2F..%2F..%2F.env");
    assert.equal(r.status, 404);
    assert.match(r.headers.get("content-type"), /problem\+json/);
  });

  it("answers an unknown run with problem+json", async () => {
    const r = await get("/api/run/definitely-not-a-run");
    assert.equal(r.status, 404);
    const body = await r.json();
    assert.equal(body.type, "/problems/not-found");
    assert.ok(body.requestId);
  });
});

describe("input validation on the endpoint that spawns a process", () => {
  it("rejects a non-numeric limit instead of silently defaulting", async () => {
    // The old code did `parseInt(x) || 300`, which quietly ran a different
    // batch than the caller asked for.
    const r = await fetch(`${base}/api/evaluate?limit=abc`, { method: "POST" });
    assert.equal(r.status, 400);
    const body = await r.json();
    assert.equal(body.type, "/problems/invalid-request");
    assert.match(body.detail, /limit/);
  });

  it("rejects an out-of-range limit", async () => {
    const r = await fetch(`${base}/api/evaluate?limit=999999`, { method: "POST" });
    assert.equal(r.status, 400);
  });

  it("rejects an unknown engine rather than falling through", async () => {
    const r = await fetch(`${base}/api/evaluate?engine=rm-rf`, { method: "POST" });
    assert.equal(r.status, 400);
    assert.match((await r.json()).detail, /engine/);
  });

  it("cannot be reached by GET, because EventSource speaks GET", async () => {
    // This is the guard that stops a reconnect re-running the job.
    const r = await get("/api/evaluate");
    assert.equal(r.status, 404);
  });
});

describe("argument whitelisting", () => {
  it("never passes caller input through as a flag", () => {
    const args = buildArgs({ repo: "/repo", limit: 150, seed: 42, engine: "replay" });
    assert.ok(args.every((a) => typeof a === "string"));
    assert.deepEqual(args.slice(1), [
      "--limit", "150", "--seed", "42", "--save", "latest", "--engine", "replay", "--no-ablation",
    ]);
  });

  it("maps the credential-free engines to their own flags", () => {
    assert.ok(buildArgs({ repo: "/r", limit: 1, seed: 1, engine: "none" }).includes("--no-llm"));
    assert.ok(buildArgs({ repo: "/r", limit: 1, seed: 1, engine: "stub" }).includes("--stub"));
  });
});

describe("job status", () => {
  it("reports idle with a bounded history", async () => {
    const r = await get("/api/job");
    const body = await r.json();
    assert.equal(body.running, false);
    assert.ok(Array.isArray(body.history));
  });

  it("streams an idle event and closes when nothing is running", async () => {
    const r = await get("/api/stream");
    const text = await r.text();
    assert.match(text, /event: idle/);
  });
});

describe("error contract", () => {
  it("answers an unknown route in problem+json", async () => {
    const r = await get("/api/nope");
    assert.equal(r.status, 404);
    assert.match(r.headers.get("content-type"), /application\/problem\+json/);
    const body = await r.json();
    for (const k of ["type", "title", "status", "requestId"]) {
      assert.ok(k in body, `expected ${k} in problem body`);
    }
  });
});
