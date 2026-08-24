import { useState } from "react";
import { rs } from "../api.js";

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
        (e) => e.proposed_action !== e.final_action
      ).length,
    }))
    .sort((a, b) => b.blocked - a.blocked)[0].n;
};

export default function AuditTrail({ audits }) {
  const names = Object.keys(audits || {});
  const [policy, setPolicy] = useState(() => mostInteresting(audits));
  const [blockedOnly, setBlockedOnly] = useState(true);

  if (!names.length) return <div className="empty">No audit trail in this run.</div>;

  const audit = audits[policy];
  const entries = (audit?.entries || []).filter((e) =>
    blockedOnly ? e.proposed_action !== e.final_action : true
  );

  const resultText = (e) => {
    if (e.succeeded) return `recovered Rs ${rs(e.recovered_paise)}`;
    if (e.executed) return "no recovery";
    if (e.final_action === "escalate") return "escalated to a human";
    return "stopped";
  };

  return (
    <>
      <div className="controls" style={{ marginBottom: 14 }}>
        <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button onClick={() => setBlockedOnly((v) => !v)}>
          {blockedOnly ? "showing overruled only" : "showing all decisions"}
        </button>
        <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
          {entries.length} shown of {audit?.total_entries ?? 0} decisions
        </span>
      </div>

      {entries.length === 0 && (
        <div className="empty">
          Nothing was overruled for this policy. It never proposed a blocked action.
        </div>
      )}

      {entries.slice(0, 40).map((e) => {
        const overruled = e.proposed_action !== e.final_action;
        const cls = overruled ? " blocked" : e.succeeded ? " recovered" : "";
        return (
          <div key={e.seq} className={`audit-row${cls}`}>
            <div className="audit-head">
              <span className="reason">
                {e.payment_id} / {e.reason}
              </span>
              <span className="amt">Rs {rs(e.amount_paise)}</span>
            </div>
            <div className="audit-line">
              <span className="lbl">diagnosis</span>
              {e.diagnosed_class || "n/a"} (conf {Number(e.confidence).toFixed(2)})
            </div>
            <div className="audit-line">
              <span className="lbl">proposed</span>
              {e.proposed_action}
              {e.channel ? ` via ${e.channel}` : ""}
            </div>
            {overruled ? (
              <div className="audit-line">
                <span className="lbl">ruling</span>
                <span className="rule">
                  {String(e.verdict).toUpperCase()} [{e.rule}] &rarr; {e.final_action}
                </span>
                <div style={{ marginLeft: 74, color: "var(--text-muted)", marginTop: 3 }}>
                  {e.policy_explanation}
                </div>
              </div>
            ) : (
              <div className="audit-line">
                <span className="lbl">ruling</span>
                allowed
              </div>
            )}
            <div className="audit-line">
              <span className="lbl">result</span>
              {resultText(e)}
              <span style={{ color: "var(--text-muted)" }}>
                {" "}
                / cost Rs {(e.cost_paise / 100).toFixed(2)}
              </span>
            </div>
          </div>
        );
      })}
    </>
  );
}
