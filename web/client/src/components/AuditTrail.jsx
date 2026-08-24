import { useMemo, useState } from "react";

import { policyLabel, rs } from "../api.js";
import { Badge, Empty } from "./ui.jsx";

/**
 * The compliance answer, not the activity log.
 *
 * "We retried 412 payments" does not answer a question from a risk team. "We
 * declined to retry these 62, and here is the rule that stopped each one" does.
 * Overruled decisions are therefore the default view -- the interesting half of
 * an audit trail is what did not happen.
 */

/** Most-overruled policy first: an empty trail is a bad landing state. */
const mostInteresting = (audits) => {
  const names = Object.keys(audits || {});
  if (!names.length) return "";
  return names
    .map((n) => ({
      n,
      blocked: (audits[n].entries || []).filter(
        (e) => e.proposed_action !== e.final_action,
      ).length,
    }))
    .sort((a, b) => b.blocked - a.blocked)[0].n;
};

const toneOf = (e) => {
  if (e.proposed_action !== e.final_action) return "veto";
  if (e.succeeded) return "ok";
  if (e.verdict === "defer") return "defer";
  return "none";
};

const verdictBadge = (e) => {
  const t = toneOf(e);
  if (t === "veto") return <Badge tone="danger">{e.verdict}</Badge>;
  if (t === "defer") return <Badge tone="warning">deferred</Badge>;
  if (t === "ok") return <Badge tone="success">recovered</Badge>;
  return <Badge tone="neutral">allowed</Badge>;
};

const resultText = (e) => {
  if (e.succeeded) return `recovered ₹${rs(e.recovered_paise)}`;
  if (e.executed) return "no recovery";
  if (e.final_action === "escalate") return "escalated to a human";
  return "stopped";
};

const PAGE = 25;

export default function AuditTrail({ audits }) {
  const names = Object.keys(audits || {});
  const [policy, setPolicy] = useState(() => mostInteresting(audits));
  const [view, setView] = useState("blocked");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE);

  const audit = audits?.[policy];

  const entries = useMemo(() => {
    let list = audit?.entries || [];
    if (view === "blocked") list = list.filter((e) => e.proposed_action !== e.final_action);
    else if (view === "recovered") list = list.filter((e) => e.succeeded);
    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (e) =>
          e.payment_id.toLowerCase().includes(q) ||
          e.reason.toLowerCase().includes(q) ||
          (e.rule || "").toLowerCase().includes(q),
      );
    }
    return list;
  }, [audit, view, query]);

  if (!names.length) return <Empty>No audit trail in this run.</Empty>;

  return (
    <>
      <div className="audit-controls">
        <span className="select">
          <select
            value={policy}
            onChange={(e) => {
              setPolicy(e.target.value);
              setLimit(PAGE);
            }}
          >
            {names.map((n) => (
              <option key={n} value={n}>
                {policyLabel(n)}
              </option>
            ))}
          </select>
        </span>

        <span className="pill-toggle">
          {[
            ["blocked", "Overruled"],
            ["recovered", "Recovered"],
            ["all", "All"],
          ].map(([k, label]) => (
            <button
              key={k}
              data-active={view === k}
              onClick={() => {
                setView(k);
                setLimit(PAGE);
              }}
            >
              {label}
            </button>
          ))}
        </span>

        <span className="select" style={{ flex: "0 1 200px" }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="payment, reason, rule…"
            style={{
              height: 34,
              width: "100%",
              padding: "0 12px",
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-1)",
              fontSize: "var(--fs-sm)",
              fontFamily: "var(--sans)",
            }}
          />
        </span>

        <span className="audit-count">
          {entries.length} of {audit?.total_entries ?? 0}
        </span>
      </div>

      {entries.length === 0 ? (
        <Empty>
          {view === "blocked"
            ? "Nothing was overruled for this policy — it never proposed a blocked action."
            : "No entries match this filter."}
        </Empty>
      ) : (
        <>
          <div className="audit-list">
            {entries.slice(0, limit).map((e, i) => (
              <div
                key={e.seq}
                className="audit-entry"
                data-tone={toneOf(e)}
                style={{ animationDelay: `${Math.min(i, 12) * 0.022}s` }}
              >
                <div className="audit-head">
                  <span className="audit-id">{e.payment_id}</span>
                  <span className="audit-reason">{e.reason}</span>
                  {verdictBadge(e)}
                  <span className="audit-amount">₹{rs(e.amount_paise)}</span>
                </div>

                <div className="audit-rows">
                  <span className="audit-k">Diagnosis</span>
                  <span className="audit-v">
                    {e.diagnosed_class || "n/a"}
                    <span className="muted" style={{ color: "var(--text-4)" }}>
                      {" "}
                      · confidence {Number(e.confidence).toFixed(2)}
                    </span>
                  </span>

                  <span className="audit-k">Proposed</span>
                  <span className="audit-v">
                    <span className="mono">{e.proposed_action}</span>
                    {e.channel ? ` via ${e.channel}` : ""}
                  </span>

                  <span className="audit-k">Ruling</span>
                  <span className="audit-v">
                    {e.proposed_action !== e.final_action ? (
                      <>
                        <span className="mono" style={{ color: "var(--danger)" }}>
                          {e.rule}
                        </span>
                        {" → "}
                        <span className="mono">{e.final_action}</span>
                        <div className="audit-why">{e.policy_explanation}</div>
                      </>
                    ) : (
                      <span style={{ color: "var(--text-3)" }}>allowed as proposed</span>
                    )}
                  </span>

                  <span className="audit-k">Result</span>
                  <span className="audit-v">
                    {resultText(e)}
                    <span style={{ color: "var(--text-4)" }}>
                      {" "}
                      · cost ₹{(e.cost_paise / 100).toFixed(2)}
                    </span>
                  </span>
                </div>
              </div>
            ))}
          </div>

          {entries.length > limit && (
            <button
              className="btn"
              style={{ marginTop: "var(--s4)", width: "100%", justifyContent: "center" }}
              onClick={() => setLimit((l) => l + PAGE)}
            >
              Show {Math.min(PAGE, entries.length - limit)} more
            </button>
          )}
        </>
      )}
    </>
  );
}
