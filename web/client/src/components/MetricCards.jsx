import { policyLabel, rs } from "../api.js";
import { useCountUp } from "../hooks.js";
import { Icon } from "./ui.jsx";

/**
 * The headline figures. Stat tiles rather than a chart -- these are four
 * unrelated single values whose job is magnitude at a glance, and a bar chart
 * of four incomparable quantities would be strictly worse than the numbers.
 *
 * Each carries a semantic accent: risk is amber, recovery is green, violations
 * are red when non-zero and green when clean. The accent drives the top rail,
 * the glow and the icon, so the colour reads as meaning rather than decoration.
 */

function Sparkline({ points, color }) {
  if (!points || points.length < 2) return null;
  const w = 120;
  const h = 22;
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const span = max - min || 1;
  const step = w / (points.length - 1);
  const xy = points.map((p, i) => [i * step, h - ((p - min) / span) * (h - 3) - 1.5]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w},${h} L0,${h} Z`;
  const id = `sp-${color.replace(/[^a-z0-9]/gi, "")}`;

  return (
    <svg className="metric-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx={xy[xy.length - 1][0]} cy={xy[xy.length - 1][1]} r="2.4" fill={color} />
    </svg>
  );
}

function Metric({ label, value, prefix = "", foot, accent, icon: Ico, spark, small, animate = true }) {
  const shown = useCountUp(animate ? value : 0);
  const display = animate
    ? `${prefix}${rs(Math.round(shown))}`
    : `${prefix}${value}`;

  return (
    <div className="metric" style={{ "--accent": accent }}>
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        <span className="metric-icon">
          <Ico />
        </span>
      </div>
      <div className={`metric-value${small ? " small" : ""}`}>{display}</div>
      <div className="metric-foot">{foot}</div>
      {spark && <Sparkline points={spark} color={accent} />}
    </div>
  );
}

export default function MetricCards({ run }) {
  if (!run) return null;

  const policies = run.policies;
  const best = [...policies].sort((a, b) => b.ledger.net_paise - a.ledger.net_paise)[0];
  const violations = policies.reduce((n, p) => n + p.ledger.policy_violations, 0);
  const recoverable = Math.round(run.batch.recoverable_value_rupees * 100);
  const atRisk = Math.round(run.batch.total_value_rupees * 100);

  // Net recovery across policies, worst to best -- a real shape from the data
  // rather than a decorative squiggle.
  const spark = [...policies]
    .sort((a, b) => a.ledger.net_paise - b.ledger.net_paise)
    .map((p) => p.ledger.net_paise / 100);

  const recoveredPct = (best.ledger.gross_recovered_paise / recoverable) * 100;

  return (
    <div className="metrics">
      <div className="rise rise-1">
        <Metric
          label="Value at risk"
          prefix="₹"
          value={atRisk}
          foot={`${run.batch_size} failed payments in this batch`}
          accent="var(--warning)"
          icon={Icon.Risk}
        />
      </div>
      <div className="rise rise-2">
        <Metric
          label="Recoverable"
          prefix="₹"
          value={recoverable}
          foot={`${((recoverable / atRisk) * 100).toFixed(0)}% of the batch — excludes risk blocks & our own bugs`}
          accent="var(--series-1)"
          icon={Icon.Wallet}
        />
      </div>
      <div className="rise rise-3">
        <Metric
          label="Best net recovery"
          prefix="₹"
          value={best.ledger.net_paise}
          foot={`${policyLabel(best.policy)} · ${recoveredPct.toFixed(1)}% of recoverable`}
          accent="var(--success)"
          icon={Icon.Trend}
          spark={spark.length > 1 ? spark : null}
        />
      </div>
      <div className="rise rise-4">
        <Metric
          label="Policy violations"
          value={violations}
          animate={false}
          foot={
            violations
              ? "by policies running without guardrails, by design"
              : "none across every policy in this run"
          }
          accent={violations ? "var(--danger)" : "var(--success)"}
          icon={Icon.Shield}
        />
      </div>
    </div>
  );
}
