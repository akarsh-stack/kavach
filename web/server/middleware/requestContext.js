/**
 * Give every request an identity, a clock and a bound logger.
 *
 * Without this, two concurrent requests interleave their log lines and there
 * is no way to tell which line belongs to which. The id is echoed in the
 * `X-Request-Id` response header and in every problem+json body, so a user can
 * paste it into a bug report and it can be grepped straight out of the logs.
 *
 * An inbound `X-Request-Id` is honoured -- if something upstream already
 * assigned one, keeping it is what makes a trace span more than one hop -- but
 * it is length-capped and sanitised, because it ends up in a response header
 * and in log output.
 */

import { randomUUID } from "node:crypto";

const SAFE_ID = /^[A-Za-z0-9._-]{1,128}$/;

/**
 * Records the router's mount path while it is still meaningful.
 *
 * Applied at the top of every mounted router. Three lines, and without it the
 * metric label for an endpoint changes depending on which middleware answered.
 */
export function rememberMount(req, _res, next) {
  req.mountPath = req.baseUrl;
  next();
}

export function requestContext({ logger, metrics }) {
  return (req, res, next) => {
    const inbound = req.get("x-request-id");
    req.id = inbound && SAFE_ID.test(inbound) ? inbound : randomUUID();
    req.log = logger.child({ requestId: req.id });
    req.startedAt = process.hrtime.bigint();

    res.set("X-Request-Id", req.id);

    res.on("finish", () => {
      const seconds = Number(process.hrtime.bigint() - req.startedAt) / 1e9;
      // Label with the matched route pattern, never the raw URL. Using the path
      // would give a distinct time series per artefact name and blow up
      // cardinality in whatever scrapes this.
      // `req.mountPath` is stashed by rememberMount while the router is still
      // on the stack. Reading req.baseUrl here instead returns "" -- Express
      // restores it once the router completes, so the label loses its prefix
      // and the same endpoint reports under two names.
      const route = req.route?.path ? `${req.mountPath ?? ""}${req.route.path}` : "unmatched";
      metrics.httpRequests.inc({ route, method: req.method, status: res.statusCode });
      metrics.httpDuration.observe({ route, method: req.method }, seconds);

      // Health and metrics are polled constantly; logging them at info level
      // buries everything that matters.
      const noisy = req.path === "/api/health" || req.path === "/api/metrics";
      const level = res.statusCode >= 500 ? "error" : res.statusCode >= 400 ? "warn" : noisy ? "debug" : "info";
      req.log[level]("request", {
        method: req.method,
        path: req.path,
        status: res.statusCode,
        ms: Number((seconds * 1000).toFixed(1)),
      });
    });

    next();
  };
}
