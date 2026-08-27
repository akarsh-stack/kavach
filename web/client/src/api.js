const j = async (url) => {
  const r = await fetch(url);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(`${r.status} ${body.error || r.statusText}`);
  }
  return r.json();
};

export const getRun = (name = "latest") => j(`/api/run/${name}`);
export const getSensitivity = () => j("/api/sensitivity");
export const listRuns = () => j("/api/runs");
export const getCapabilities = () => j("/api/capabilities");

/**
 * Start an evaluation, then watch it.
 *
 * POST starts; the SSE stream only observes. EventSource speaks GET and
 * reconnects on every blip, so a GET with a side effect behind it re-runs the
 * job -- which silently spawned five evaluations before this split existed.
 */
export async function evaluate({ limit, seed, engine }, onLog, onDone) {
  const qs = new URLSearchParams({ limit, seed, engine });
  const started = await fetch(`/api/evaluate?${qs}`, { method: "POST" });
  if (!started.ok) {
    const body = await started.json().catch(() => ({}));
    onDone({ code: -1, error: body.error || started.statusText });
    return () => {};
  }

  const es = new EventSource("/api/stream");
  let finished = false;
  const finish = (payload) => {
    if (finished) return;
    finished = true;
    es.close();
    onDone(payload);
  };

  es.addEventListener("log", (e) => onLog(JSON.parse(e.data)));
  es.addEventListener("done", (e) => finish(JSON.parse(e.data)));
  es.addEventListener("idle", () => finish({ code: 0 }));
  es.onerror = () => finish({ code: -1 });
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
