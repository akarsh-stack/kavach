import { useMemo, useState } from "react";

import { rs } from "../api.js";
import { useCountUp } from "../hooks.js";
import { Badge, Empty, Icon } from "./ui.jsx";

/**
 * The product surface: a recovery work queue, not an evaluation result.
 *
 * The brief asks for an agent that "detects revenue at risk, determines the
 * right intervention, and executes a bounded recovery workflow", with
 * "compliant escalation, stopping rules, and an audit trail". Those four
 * clauses are the four states here:
 *
 *   needs_human  compliant escalation -- risk reviews and integration bugs,
 *                with the reason a person is being asked to look
 *   scheduled    the bounded workflow mid-execution
 *   recovered    money actually back
 *   stopped      the stopping rules firing, each with its cause
 *
 * Ordered by what wants attention first, then by amount. The evaluation lives
 * on the Evidence tab: it is why you should believe this screen, not the
 * screen itself.
 */

const STATES = [
  { key: "needs_human", label: "Needs you", tone: "danger" },
  { key: "scheduled", label: "In flight", tone: "warning" },
  { key: "recovered", label: "Recovered", tone: "success" },
  { key: "stopped", label: "Stopped", tone: "neutral" },
];

const STATE_META = Object.fromEntries(STATES.map((s) => [s.key, s]));

const ACTION_LABEL = {
  retry: "Retry same instrument",
  switch_rail: "Offer another method",
  nudge: "Message the customer",
  escalate: "Hand to a human",
  stop: "Stop",
};

const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
};

function Stat({ label, value, prefix = "", accent, foot, animate = true }) {
  const shown = useCountUp(animate ? value : 0);
  return (
    <div className="metric" style={{ "--accent": accent }}>
      <div className="metric-top">
        <span className="metric-label">{label}</span>
      </div>
      <div className="metric-value small">
        {prefix}
        {animate ? rs(Math.round(shown)) : value}
      </div>
      {foot && <div className="metric-foot">{foot}</div>}
    </div>
  );
}

function Step({ step, last }) {
  const blocked = step.proposed !== step.action;
  const tone = step.succeeded
    ? "var(--success)"
    : blocked
      ? "var(--danger)"
      : "var(--text-4)";
  return (
    <div className="step">
      <span className="step-rail">
        <span className="step-dot" style={{ background: tone }} />
        {!last && <span className="step-line" />}
      </span>
      <div className="step-body">
        <div className="step-head">
          <span className="step-when">{when(step.at)}</span>
          <span className="step-action">
            {ACTION_LABEL[step.action] || step.action}
            {step.channel ? ` · ${step.channel}` : ""}
          </span>
          {step.succeeded && <Badge tone="success">recovered</Badge>}
          {blocked && <Badge tone="danger">{step.rule}</Badge>}
        </div>
        {blocked && step.explanation && (
          <div className="step-why">
            Model proposed <span className="mono">{step.proposed}</span> — {step.explanation}
          </div>
        )}
      </div>
    </div>
  );
}

function PaymentCard({ p, open, onToggle }) {
  const meta = STATE_META[p.state] || STATE_META.stopped;
  return (
    <div className="work-item" data-state={p.state}>
      <button className="work-head" onClick={onToggle} aria-expanded={open}>
        <Icon.Chevron open={open} style={{ color: "var(--text-4)", flex: "none" }} />

        <span className="work-id">{p.payment_id}</span>

        <span className="work-reason">
          <span className="mono">{p.reason}</span>
          <span className="work-rail">
            {p.method}
            {p.issuer ? ` · ${p.issuer}` : ""}
            {p.is_subscription ? " · subscription" : ""}
          </span>
        </span>

        <span className="work-diag">
          {p.diagnosis || "—"}
          {p.confidence > 0 && (
            <span className="work-conf">{Number(p.confidence).toFixed(2)}</span>
          )}
        </span>

        <Badge tone={meta.tone}>{meta.label}</Badge>

        <span className="work-amount">₹{rs(p.amount_paise)}</span>
      </button>

      {open && (
        <div className="work-detail">
          {p.description && <p className="work-desc">{p.description}</p>}

          {p.rationale && (
            <div className="work-rationale">
              <span className="audit-k">Agent</span>
              <span>{p.rationale}</span>
            </div>
          )}

          {p.handoff && (
            <div className="work-handoff">
              <Icon.Alert />
              <span>{p.handoff}</span>
            </div>
          )}

          <div className="steps">
            {p.steps.map((s, i) => (
              <Step key={i} step={s} last={i === p.steps.length - 1} />
            ))}
          </div>

          <div className="work-foot">
            <span>
              {p.attempts} attempt{p.attempts === 1 ? "" : "s"}
            </span>
            <span>cost ₹{(p.cost_paise / 100).toFixed(2)}</span>
            {p.recovered_paise > 0 && (
              <span style={{ color: "var(--success)" }}>
                recovered ₹{rs(p.recovered_paise)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const PAGE = 20;

export default function RecoveryConsole({ workflow }) {
  const [state, setState] = useState("needs_human");
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState(null);
  const [limit, setLimit] = useState(PAGE);

  const counts = useMemo(() => {
    const c = {};
    for (const p of workflow?.payments || []) c[p.state] = (c[p.state] || 0) + 1;
    return c;
  }, [workflow]);

  const list = useMemo(() => {
    let l = (workflow?.payments || []).filter((p) => state === "all" || p.state === state);
    const q = query.trim().toLowerCase();
    if (q) {
      l = l.filter(
        (p) =>
          p.payment_id.toLowerCase().includes(q) ||
          p.reason.toLowerCase().includes(q) ||
          (p.diagnosis || "").toLowerCase().includes(q) ||
          (p.issuer || "").toLowerCase().includes(q),
      );
    }
    return l;
  }, [workflow, state, query]);

  if (!workflow) return <Empty>No recovery run loaded yet.</Empty>;
  const t = workflow.totals;

  return (
    <>
      <div className="metrics">
        <div className="rise rise-1">
          <Stat
            label="Revenue at risk"
            prefix="₹"
            value={t.at_risk_paise}
            accent="var(--warning)"
            foot={`${t.payments - t.recovered_count} payments unresolved`}
          />
        </div>
        <div className="rise rise-2">
          <Stat
            label="Recovered"
            prefix="₹"
            value={t.recovered_paise}
            accent="var(--success)"
            foot={`${t.recovered_count} payments · ₹${rs(t.spent_paise)} spent chasing`}
          />
        </div>
        <div className="rise rise-3">
          <Stat
            label="Needs a human"
            value={t.needs_human}
            animate={false}
            accent={t.needs_human ? "var(--danger)" : "var(--success)"}
            foot="risk reviews and integration faults"
          />
        </div>
        <div className="rise rise-4">
          <Stat
            label="Actions blocked"
            value={t.blocked_actions}
            animate={false}
            accent="var(--series-3)"
            foot="stopping rules that fired on the model"
          />
        </div>
      </div>

      <section className="card rise rise-5">
        <div className="card-head">
          <h2>Recovery queue</h2>
          <p className="note">
            Every failed payment the agent is working, ordered by what wants attention
            first. Expand one to see the bounded workflow it executed — and where the
            policy layer overruled the model.
          </p>
        </div>

        <div className="audit-controls">
          <span className="pill-toggle">
            {[...STATES, { key: "all", label: "All" }].map((s) => (
              <button
                key={s.key}
                data-active={state === s.key}
                onClick={() => {
                  setState(s.key);
                  setLimit(PAGE);
                }}
              >
                {s.label}
                {s.key !== "all" && counts[s.key] ? ` ${counts[s.key]}` : ""}
              </button>
            ))}
          </span>

          <input
            className="field"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="payment, reason, bank…"
          />

          <span className="audit-count">
            {list.length} of {t.payments}
          </span>
        </div>

        {list.length === 0 ? (
          <Empty>
            {state === "needs_human"
              ? "Nothing is waiting on a human. Every escalation has been handled."
              : "No payments match this filter."}
          </Empty>
        ) : (
          <>
            <div className="work-list">
              {list.slice(0, limit).map((p) => (
                <PaymentCard
                  key={p.payment_id}
                  p={p}
                  open={openId === p.payment_id}
                  onToggle={() =>
                    setOpenId((id) => (id === p.payment_id ? null : p.payment_id))
                  }
                />
              ))}
            </div>
            {list.length > limit && (
              <button
                className="btn"
                style={{ marginTop: "var(--s4)", width: "100%", justifyContent: "center" }}
                onClick={() => setLimit((l) => l + PAGE)}
              >
                Show {Math.min(PAGE, list.length - limit)} more
              </button>
            )}
          </>
        )}
      </section>
    </>
  );
}
