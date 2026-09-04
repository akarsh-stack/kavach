/**
 * Token bucket, applied to the endpoint that spawns a process.
 *
 * `POST /api/evaluate` forks Python, writes a file and burns provider quota.
 * The job manager already refuses concurrent runs with 409, but that is a
 * correctness guard, not a cost guard: a client in a retry loop can still
 * hammer the endpoint, and each attempt does real work before being refused.
 *
 * A bucket rather than a fixed window because a fixed window lets a caller
 * spend the whole allowance in the last millisecond of one window and again in
 * the first of the next -- double the intended burst at the boundary.
 *
 * Keyed by client IP, held in memory. Correct for a single process, which is
 * what this is. A multi-instance deployment needs shared state, and that is
 * noted rather than pretended: an in-memory limiter behind a load balancer
 * silently multiplies the limit by the instance count.
 */

import { tooManyRequests } from "../lib/problem.js";

export function rateLimit({ capacity, refillPerMinute, metrics, now = Date.now }) {
  const refillPerMs = refillPerMinute / 60_000;
  /** @type {Map<string, {tokens:number, updated:number}>} */
  const buckets = new Map();

  // Buckets for callers that have gone away are garbage. Swept lazily on write
  // so there is no timer to leak in tests or hold the event loop open.
  const sweep = (t) => {
    if (buckets.size < 1024) return;
    for (const [k, b] of buckets) {
      if (t - b.updated > 10 * 60_000) buckets.delete(k);
    }
  };

  return (req, _res, next) => {
    const t = now();
    const key = req.ip || req.socket?.remoteAddress || "unknown";
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = { tokens: capacity, updated: t };
      buckets.set(key, bucket);
      sweep(t);
    }

    bucket.tokens = Math.min(capacity, bucket.tokens + (t - bucket.updated) * refillPerMs);
    bucket.updated = t;

    if (bucket.tokens < 1) {
      const waitMs = Math.ceil((1 - bucket.tokens) / refillPerMs);
      metrics?.rateLimited.inc();
      req.log?.warn("rate limited", { key, retryAfterMs: waitMs });
      // Retry-After is the whole point: tell the client when to come back
      // rather than making it guess. This project has been on the receiving
      // end of providers that do not, and it cost a live run.
      _res.set("Retry-After", String(Math.ceil(waitMs / 1000)));
      return next(
        tooManyRequests("too many evaluation requests from this client", {
          retryAfterSeconds: Math.ceil(waitMs / 1000),
        }),
      );
    }

    bucket.tokens -= 1;
    next();
  };
}
