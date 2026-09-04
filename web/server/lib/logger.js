/**
 * Structured logging, one JSON object per line, no dependency.
 *
 * `console.log("[evaluate] spawn: ...")` is fine until you need to answer "how
 * long did that request take" or "which request produced this error", at which
 * point prose is a dead end. Every line here carries a level, a timestamp and
 * whatever context the caller bound -- crucially the request id -- so a single
 * grep reconstructs one request's whole story.
 *
 * `child()` binds context once at the edge and every log below inherits it,
 * which is what makes request correlation free rather than something each
 * handler has to remember.
 *
 * A pretty mode exists for a TTY, because JSON lines are miserable to read
 * while developing and nobody tails production on a terminal.
 */

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

const COLOUR = {
  debug: "\x1b[2m",
  info: "\x1b[36m",
  warn: "\x1b[33m",
  error: "\x1b[31m",
};
const RESET = "\x1b[0m";

/** Values that must never reach a log line, whatever a caller passes. */
const REDACT = /(api[_-]?key|secret|token|authorization|password)/i;

function scrub(value, depth = 0) {
  if (depth > 4 || value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((v) => scrub(v, depth + 1));
  const out = {};
  for (const [k, v] of Object.entries(value)) {
    out[k] = REDACT.test(k) ? "[redacted]" : scrub(v, depth + 1);
  }
  return out;
}

export function createLogger({ level = "info", pretty = false, base = {}, sink = process.stdout }) {
  const threshold = LEVELS[level] ?? LEVELS.info;

  const emit = (lvl, msg, fields) => {
    if (LEVELS[lvl] < threshold) return;
    const record = {
      ts: new Date().toISOString(),
      level: lvl,
      msg,
      ...scrub(base),
      ...scrub(fields || {}),
    };
    if (!pretty) {
      sink.write(`${JSON.stringify(record)}\n`);
      return;
    }
    const { ts, level: _l, msg: _m, ...rest } = record;
    const tail = Object.keys(rest).length ? ` ${JSON.stringify(rest)}` : "";
    sink.write(`${COLOUR[lvl]}${lvl.padEnd(5)}${RESET} ${ts.slice(11, 23)} ${msg}${tail}\n`);
  };

  const api = {
    debug: (msg, fields) => emit("debug", msg, fields),
    info: (msg, fields) => emit("info", msg, fields),
    warn: (msg, fields) => emit("warn", msg, fields),
    error: (msg, fields) => emit("error", msg, fields),
    /** Bind context for everything logged downstream of here. */
    child: (extra) => createLogger({ level, pretty, base: { ...base, ...extra }, sink }),
  };
  return api;
}
