import { rs } from "../api.js";

/**
 * The headline numbers. A stat tile, not a chart -- these are single values
 * whose job is magnitude at a glance, and a bar chart of four unrelated
 * quantities would be worse than the numbers themselves.
 */
export default function StatTiles({ run }) {
  if (!run) return null;
  const policies = run.policies;
  const best = [...policies].sort((a, b) => b.ledger.net_paise - a.ledger.net_paise)[0];
  const violations = policies.reduce((n, p) => n + p.ledger.policy_violations, 0);
  const recoverable = Math.round(run.batch.recoverable_value_rupees * 100);

  const tiles = [
    {
      label: "value at risk",
      value: `₹${rs(Math.round(run.batch.total_value_rupees * 100))}`,
      foot: `${run.batch_size} failed payments`,
    },
    {
      label: "recoverable",
      value: `₹${rs(recoverable)}`,
      foot: "excludes risk blocks & our own bugs",
    },
    {
      label: "best net recovery",
      value: `₹${rs(best.ledger.net_paise)}`,
      foot: `${best.policy} · ${((best.ledger.gross_recovered_paise / recoverable) * 100).toFixed(1)}% of recoverable`,
    },
    {
      label: "policy violations",
      value: String(violations),
      foot: violations ? "by policies running without guardrails" : "none across all policies",
      status: violations > 0 ? "warn" : "ok",
    },
  ];

  return (
    <div className="tiles">
      {tiles.map((t) => (
        <div className="tile" key={t.label}>
          <div className="label">{t.label}</div>
          <div className={`value${t.value.length > 11 ? " small" : ""}`}>{t.value}</div>
          <div className="foot">{t.foot}</div>
        </div>
      ))}
    </div>
  );
}
