/**
 * One place where every failure becomes a response.
 *
 * Two rules, both learned the hard way in this project:
 *
 * 1. **Never leak an internal error to the client.** A stack trace in a
 *    response body tells an attacker your directory layout. Unexpected errors
 *    become a generic 500 carrying only the request id, which is enough for
 *    someone to find the real cause in the logs.
 *
 * 2. **Always answer in the same shape.** The dashboard previously guessed at
 *    failures by reading status codes, and was wrong twice -- once assuming a
 *    dead proxy returns 502 when Vite returns 500, once assuming a rate limit
 *    would not answer 404. A stable `type` field means a client never has to
 *    guess again.
 */

import { Problem, internal, notFound } from "../lib/problem.js";

export function notFoundHandler() {
  return (req, _res, next) => {
    next(notFound(`no route for ${req.method} ${req.path}`));
  };
}

export function errorHandler() {
  // Express identifies an error handler by arity; `next` must stay.
  // eslint-disable-next-line no-unused-vars
  return (err, req, res, _next) => {
    const problem = err instanceof Problem ? err : internal("an unexpected error occurred");

    if (!problem.expected) {
      // Log the real thing, return the sanitised thing.
      req.log?.error("unhandled error", {
        err: err?.message,
        stack: err?.stack?.split("\n").slice(0, 6).join(" | "),
      });
    } else {
      req.log?.debug("request rejected", { type: problem.type, detail: problem.detail });
    }

    if (res.headersSent) {
      // Mid-stream, typically SSE. Nothing useful can be sent; close cleanly
      // rather than corrupting the framing with a JSON body.
      return res.end();
    }

    res
      .status(problem.status)
      .type("application/problem+json")
      .json({ ...problem.toJSON(req.originalUrl), requestId: req.id });
  };
}
