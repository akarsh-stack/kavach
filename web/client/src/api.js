const j = async (url) => {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).error || r.statusText}`);
  return r.json();
};

export const getRun = (name = "latest") => j(`/api/run/${name}`);
export const getSensitivity = () => j("/api/sensitivity");
export const listRuns = () => j("/api/runs");

/** Stream an evaluation. Returns an unsubscribe function. */
export function evaluate({ limit, seed, engine }, onLog, onDone) {
  const qs = new URLSearchParams({ limit, seed, engine });
  const es = new EventSource(`/api/evaluate?${qs}`);
  es.addEventListener("log", (e) => onLog(JSON.parse(e.data)));
  es.addEventListener("done", (e) => {
    onDone(JSON.parse(e.data));
    es.close();
  });
  es.onerror = () => {
    onDone({ code: -1 });
    es.close();
  };
  return () => es.close();
}

export const rs = (paise) =>
  (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });

export const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
];

/**
 * Colour follows the entity, never its rank -- a filter that changes which
 * policies are on screen must not repaint the survivors. So the slot is keyed
 * off a fixed policy list, not the index in whatever array we happen to render.
 */
const POLICY_ORDER = [
  "agent",
  "rules_engine",
  "fixed_retry",
  "naive_llm",
  "agent_no_guardrails",
  "no_retry",
];

export const policyColor = (name) => {
  const i = POLICY_ORDER.indexOf(name);
  return SERIES[(i < 0 ? POLICY_ORDER.length : i) % SERIES.length];
};
