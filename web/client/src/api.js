/**
 * An error the caller should wait out rather than report.
 *
 * `npm run dev` starts the API and Vite concurrently, so the browser can load
 * the page a second before Express binds. Every /api call then fails, and the
 * dashboard used to settle into a permanent error telling the reader to go run
 * a Python script -- advice that fixes nothing, for a problem that fixes itself
 * in about a second. Only an F5 cleared it.
 */
export class Unreachable extends Error {}

const j = async (url) => {
  let r;
  try {
    r = await fetch(url);
  } catch (e) {
    // fetch rejects outright when the dev proxy has nothing to talk to.
    throw new Unreachable(String(e.message || e));
  }

  // Asked an /api path for JSON and got a document back? Then there is no API
  // here — this is a static host answering with the SPA shell. It arrives as a
  // perfectly healthy 200, which is why this cannot be a status check: the
  // previous version of this file already shipped one bug from assuming a
  // particular status code, and a rewrite rule makes status meaningless.
  const kind = r.headers.get("content-type") || "";
  if (url.startsWith("/api/") && !kind.includes("json")) {
    throw new Unreachable(`no API at ${url} (served ${kind.split(";")[0] || "?"})`);
  }

  if (!r.ok) {
    const body = await r.json().catch(() => null);
    // Vite reports a dead proxy target as a plain 500 -- not 502, which is what
    // this originally looked for, and why the first version of this fix did
    // nothing. Status alone cannot separate "nothing is listening" from a real
    // server fault. The body can: every error this API raises is JSON carrying
    // an `error` field, and a proxy failure is not.
    if (r.status >= 500 && !body?.error) {
      throw new Unreachable(`API not reachable (${r.status})`);
    }
    throw new Error(`${r.status} ${body?.error || r.statusText}`);
  }
  return r.json();
};

/** Retry only while the backend is still coming up; surface anything else. */
export async function waitForApi(fn, { tries = 12, gap = 400 } = {}) {
  for (let i = 0; ; i++) {
    try {
      return await fn();
    } catch (e) {
      if (!(e instanceof Unreachable) || i >= tries) throw e;
      await new Promise((r) => setTimeout(r, gap));
    }
  }
}

/* -------------------------------------------------------------------------
   Static fallback
   -------------------------------------------------------------------------

   The dashboard has two homes. On a laptop it talks to the Express API, which
   can spawn Python and actually run an evaluation. Deployed to a static host
   there is no API at all — but everything a reader needs to SEE is a committed
   JSON artefact that never changes at runtime, so the build stages those files
   into `public/data/` and we read them directly.

   Only the run button needs a server, and it is the only thing that degrades.
*/

/** True once we have established there is no API behind this page. */
let staticMode = false;
export const isStaticMode = () => staticMode;

/** Try the API; on a genuine "nothing is listening", read the staged file. */
const withStatic = async (apiPath, staticPath) => {
  if (staticMode) return j(staticPath);
  try {
    return await j(apiPath);
  } catch (e) {
    if (!(e instanceof Unreachable)) throw e;
    const data = await j(staticPath);
    staticMode = true;
    return data;
  }
};

export const getRun = (name = "latest") =>
  // `latest` only exists where runs happen. A static deployment has exactly
  // one run — the published one — so both names resolve to it.
  withStatic(`/api/run/${name}`, "/data/reference.json");

export const getSensitivity = () => withStatic("/api/sensitivity", "/data/sensitivity.json");

export const listRuns = () => j("/api/runs");

export const getCapabilities = async () => {
  if (!staticMode) {
    try {
      return await j("/api/capabilities");
    } catch (e) {
      if (!(e instanceof Unreachable)) throw e;
      staticMode = true;
    }
  }
  // Nothing can be launched from here, and the control should say so rather
  // than offer engines that will fail.
  return {
    static: true,
    engines: [{ id: "replay", label: "Published run", available: true }],
    cache: { entries: 0, model: null, limit: null },
  };
};

/**
 * Start an evaluation, then watch it.
 *
 * POST starts; the SSE stream only observes. EventSource speaks GET and
 * reconnects on every blip, so a GET with a side effect behind it re-runs the
 * job -- which silently spawned five evaluations before this split existed.
 *
 * A poll runs alongside the stream as a watchdog. If the Python process dies
 * before the stream attaches, or a `done` frame is lost, the stream alone
 * leaves the button spinning forever with no way back. The dashboard hung
 * exactly that way when an Anthropic run aborted on a billing error. The poll
 * asks the server a direct question -- is a job running? -- and no missed
 * event can lie about the answer.
 */
export async function evaluate({ limit, seed, engine }, onLog, onDone) {
  const qs = new URLSearchParams({ limit, seed, engine });

  let started;
  try {
    started = await fetch(`/api/evaluate?${qs}`, { method: "POST" });
  } catch (e) {
    onDone({ code: -1, error: String(e.message || e) });
    return () => {};
  }
  if (!started.ok) {
    const body = await started.json().catch(() => ({}));
    onDone({ code: -1, error: body.error || started.statusText });
    return () => {};
  }

  const es = new EventSource("/api/stream");
  let finished = false;
  let poll;

  const finish = (payload) => {
    if (finished) return;
    finished = true;
    clearInterval(poll);
    es.close();
    onDone(payload);
  };

  es.addEventListener("log", (e) => onLog(JSON.parse(e.data)));
  es.addEventListener("done", (e) => finish(JSON.parse(e.data)));
  es.addEventListener("idle", () => finish({ code: 0 }));
  // An EventSource error alone is NOT proof the job ended -- it reconnects.
  // Let the watchdog decide.

  poll = setInterval(async () => {
    try {
      const r = await fetch("/api/job");
      const { running } = await r.json();
      if (!running) finish({ code: 0 });
    } catch {
      /* transient; the next tick will retry */
    }
  }, 2500);

  return () => finish({ code: -1 });
}

/* -------------------------------------------------------------------------
   Formatting
   ------------------------------------------------------------------------- */

export const rs = (paise) =>
  (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });

/** Compact form for headline figures: 4,52,144 -> 4.52L */
export const rsCompact = (paise) => {
  const v = Math.abs(paise / 100);
  const sign = paise < 0 ? "-" : "";
  if (v >= 1e7) return `${sign}${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${sign}${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${sign}${(v / 1e3).toFixed(1)}K`;
  return `${sign}${v.toFixed(0)}`;
};

export const pct = (v, digits = 1) => `${(v * 100).toFixed(digits)}%`;

/* -------------------------------------------------------------------------
   Colour
   -------------------------------------------------------------------------

   Six validated categorical slots. Assigned in fixed order and never cycled.

   Colour follows the ENTITY, never its rank: a filter that changes which
   policies are on screen must not repaint the survivors. So the slot is keyed
   off a fixed policy list, not the index in whatever array we happen to
   render.
*/

export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
];

export const SERIES_HEX = ["#5273e8", "#18a392", "#8263e0", "#d14a82", "#c68325", "#2a9ac0"];

const POLICY_ORDER = [
  "agent",
  "rules_engine",
  "fixed_retry",
  "naive_llm",
  "agent_no_guardrails",
  "no_retry",
];

const slot = (name) => {
  const i = POLICY_ORDER.indexOf(name);
  return (i < 0 ? POLICY_ORDER.length : i) % SERIES.length;
};

export const policyColor = (name) => SERIES[slot(name)];
export const policyHex = (name) => SERIES_HEX[slot(name)];

/** Human label for a policy id. */
export const policyLabel = (name) =>
  ({
    no_retry: "No retry",
    fixed_retry: "Fixed retry",
    rules_engine: "Rules engine",
    naive_llm: "Naive LLM",
    agent: "Agent",
    agent_no_guardrails: "Agent (no guardrails)",
  })[name] || name;
