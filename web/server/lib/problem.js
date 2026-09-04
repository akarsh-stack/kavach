/**
 * Errors as data, in the shape RFC 7807 specifies.
 *
 * Every failure this API produces is one of these, so a client can branch on
 * `type` instead of pattern-matching prose. That is not a style preference: the
 * dashboard already shipped one bug from parsing an error message, and another
 * from keying on a status code that meant two different things depending on
 * which proxy answered. A stable machine-readable discriminator is the fix for
 * both.
 *
 * The response body is `application/problem+json`:
 *
 *   { type, title, status, detail, instance, ...extra }
 *
 * `type` is a relative URI rather than a bare string so it stays unique if this
 * ever merges with another API's error space.
 */

export class Problem extends Error {
  /**
   * @param {object} spec
   * @param {number} spec.status  HTTP status
   * @param {string} spec.type    stable machine-readable slug
   * @param {string} spec.title   short human summary, same for every instance
   * @param {string} [spec.detail] what went wrong *this* time
   * @param {object} [spec.extra] additional members, per RFC 7807 §3.2
   */
  constructor({ status, type, title, detail, extra = {} }) {
    super(detail || title);
    this.name = "Problem";
    this.status = status;
    this.type = `/problems/${type}`;
    this.title = title;
    this.detail = detail;
    this.extra = extra;
    // A 5xx is our fault and belongs in the logs at error level with a stack.
    // A 4xx is the caller's and is noise at anything above debug.
    this.expected = status < 500;
  }

  toJSON(instance) {
    return {
      type: this.type,
      title: this.title,
      status: this.status,
      ...(this.detail ? { detail: this.detail } : {}),
      ...(instance ? { instance } : {}),
      ...this.extra,
    };
  }
}

export const badRequest = (detail, extra) =>
  new Problem({ status: 400, type: "invalid-request", title: "Invalid request", detail, extra });

export const notFound = (detail, extra) =>
  new Problem({ status: 404, type: "not-found", title: "Not found", detail, extra });

export const conflict = (detail, extra) =>
  new Problem({ status: 409, type: "conflict", title: "Conflict", detail, extra });

export const tooManyRequests = (detail, extra) =>
  new Problem({
    status: 429,
    type: "rate-limited",
    title: "Too many requests",
    detail,
    extra,
  });

export const unavailable = (detail, extra) =>
  new Problem({
    status: 503,
    type: "unavailable",
    title: "Service unavailable",
    detail,
    extra,
  });

export const internal = (detail, extra) =>
  new Problem({
    status: 500,
    type: "internal",
    title: "Internal error",
    detail,
    extra,
  });
