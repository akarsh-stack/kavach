import { rs } from "../api.js";
import { Tip, useTip } from "./Tip.jsx";

/**
 * Where the money went, per policy.
 *
 * Six segments, assigned in fixed slot order and never cycled. A 2px surface
 * gap sits between adjacent fills so touching segments stay separable for a
 * colour-blind reader -- the palette's worst adjacent CVD separation is 8.4,
 * which sits in the band where secondary encoding is required, and the gap
 * plus the legend plus the tooltip are that encoding.
 */
const PARTS = [
  { key: "mdr_paise", label: "Razorpay MDR", color: "var(--series-1)" },
  { key: "attempt_cost_paise", label: "retry attempts", color: "var(--series-2)" },
  { key: "message_cost_paise", label: "customer messages", color: "var(--series-3)" },
  { key: "escalation_cost_paise", label: "human escalation", color: "var(--series-4)" },
  { key: "goodwill", label: "goodwill & churn", color: "var(--series-5)" },
  { key: "compliance_exposure_paise", label: "compliance exposure", color: "var(--series-6)" },
];

const ROW = 40;
const BAR = 17;
const LABEL_W = 168;
const VALUE_W = 96;
const SEG_GAP = 2;

export default function CostStack({ policies }) {
  const { tip, show, hide } = useTip();

  const rows = policies
    .map((p) => {
      const l = p.ledger;
      const vals = {
        mdr_paise: l.mdr_paise,
        attempt_cost_paise: l.attempt_cost_paise,
        message_cost_paise: l.message_cost_paise,
        escalation_cost_paise: l.escalation_cost_paise,
        goodwill: l.annoyance_cost_paise + l.churn_cost_paise,
        compliance_exposure_paise: l.compliance_exposure_paise,
      };
      return { policy: p.policy, vals, total: Object.values(vals).reduce((a, b) => a + b, 0) };
    })
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total);

  if (!rows.length) return <div className="empty">No costs incurred.</div>;

  const width = 760;
  const plotW = width - LABEL_W - VALUE_W;
  const height = rows.length * ROW + 8;
  const max = Math.max(...rows.map((r) => r.total));

  return (
    <>
      <div className="legend">
        {PARTS.map((p) => (
          <span className="item" key={p.key}>
            <span className="swatch" style={{ background: p.color }} />
            {p.label}
          </span>
        ))}
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto" }}>
        {rows.map((r, i) => {
          const y = i * ROW + 8;
          let cursor = LABEL_W;
          return (
            <g key={r.policy}>
              <text className="bar-name" x={0} y={y + 13}>
                {r.policy}
              </text>
              {PARTS.map((part) => {
                const v = r.vals[part.key];
                if (v <= 0) return null;
                const w = (v / max) * plotW;
                const x0 = cursor;
                cursor += w + SEG_GAP;
                const content = (
                  <>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>{r.policy}</div>
                    <div className="k">{part.label}</div>
                    <div className="v">₹{rs(v)}</div>
                    <div className="k" style={{ marginTop: 4 }}>
                      {((v / r.total) * 100).toFixed(0)}% of ₹{rs(r.total)} total
                    </div>
                  </>
                );
                return (
                  <rect
                    key={part.key}
                    x={x0}
                    y={y}
                    width={Math.max(1, w - SEG_GAP)}
                    height={BAR}
                    rx="3"
                    fill={part.color}
                    onMouseMove={(e) => show(e, content)}
                    onMouseLeave={hide}
                  />
                );
              })}
              <text className="bar-value" x={LABEL_W + plotW + 10} y={y + 13}>
                ₹{rs(r.total)}
              </text>
            </g>
          );
        })}
      </svg>
      <Tip tip={tip} />
    </>
  );
}
