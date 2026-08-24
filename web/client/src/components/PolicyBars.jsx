import { policyColor, rs } from "../api.js";
import { Tip, useTip } from "./Tip.jsx";

const ROW = 46;
const BAR = 15;
const GAP = 3;
const LABEL_W = 168;
const VALUE_W = 96;

/**
 * Net vs direct recovery per policy.
 *
 * Two series on ONE axis -- both are rupees, so they share a scale. The gap
 * between them is the whole argument of the project: `direct` is what a
 * merchant sees on their own P&L, `net` is what it costs once re-presenting
 * risk declines is priced. A policy where the two bars diverge is one that is
 * buying its revenue with exposure.
 */
export default function PolicyBars({ policies }) {
  const { tip, show, hide } = useTip();
  const rows = [...policies].sort((a, b) => b.ledger.net_paise - a.ledger.net_paise);

  const width = 760;
  const plotW = width - LABEL_W - VALUE_W;
  const height = rows.length * ROW + 26;

  const max = Math.max(
    1,
    ...rows.map((p) => Math.max(p.ledger.net_paise, p.ledger.net_direct_paise))
  );
  const x = (v) => Math.max(0, (v / max) * plotW);

  return (
    <>
      <div className="legend">
        <span className="item">
          <span className="swatch" style={{ background: "var(--text-secondary)" }} />
          net (after compliance exposure)
        </span>
        <span className="item">
          <span
            className="swatch"
            style={{ background: "transparent", border: "1px dashed var(--text-muted)" }}
          />
          direct (merchant P&amp;L only)
        </span>
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto" }}>
        {[0, 0.5, 1].map((f) => (
          <line
            key={f}
            x1={LABEL_W + f * plotW}
            x2={LABEL_W + f * plotW}
            y1={0}
            y2={rows.length * ROW}
            stroke="var(--grid)"
            strokeWidth="1"
          />
        ))}

        {rows.map((p, i) => {
          const y = i * ROW + 6;
          const c = policyColor(p.policy);
          const net = p.ledger.net_paise;
          const direct = p.ledger.net_direct_paise;
          const diverges = direct - net > max * 0.01;

          const tipContent = (
            <>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{p.policy}</div>
              <div className="k">net after exposure</div>
              <div className="v">₹{rs(net)}</div>
              <div className="k" style={{ marginTop: 4 }}>direct (P&amp;L only)</div>
              <div className="v">₹{rs(direct)}</div>
              <div className="k" style={{ marginTop: 4 }}>gross recovered</div>
              <div className="v">₹{rs(p.ledger.gross_recovered_paise)}</div>
              {p.ledger.policy_violations > 0 && (
                <div style={{ marginTop: 6, color: "var(--critical)" }}>
                  {p.ledger.policy_violations} policy violations
                </div>
              )}
            </>
          );

          return (
            <g
              key={p.policy}
              onMouseMove={(e) => show(e, tipContent)}
              onMouseLeave={hide}
              style={{ cursor: "default" }}
            >
              <rect x={0} y={y - 6} width={width} height={ROW} fill="transparent" />
              <text className="bar-name" x={0} y={y + 13}>
                {p.policy}
                {!p.enforce_guardrails && (
                  <tspan fill="var(--warning)" fontSize="11"> *</tspan>
                )}
              </text>

              {/* direct: outline only, so it reads as a reference not a second value */}
              {diverges && (
                <rect
                  x={LABEL_W}
                  y={y + BAR + GAP}
                  width={x(direct)}
                  height={BAR}
                  rx="4"
                  fill="none"
                  stroke="var(--text-muted)"
                  strokeWidth="1"
                  strokeDasharray="3 2"
                />
              )}

              <rect
                x={LABEL_W}
                y={y}
                width={x(net)}
                height={BAR}
                rx="4"
                fill={c}
              />

              <text className="bar-value" x={LABEL_W + plotW + 10} y={y + 12}>
                ₹{rs(net)}
              </text>
            </g>
          );
        })}

        <line
          x1={LABEL_W}
          x2={LABEL_W}
          y1={0}
          y2={rows.length * ROW}
          stroke="var(--axis)"
          strokeWidth="1"
        />
        <text className="axis-label" x={LABEL_W} y={rows.length * ROW + 16}>0</text>
        <text className="axis-label" x={LABEL_W + plotW} y={rows.length * ROW + 16} textAnchor="end">
          ₹{rs(max)}
        </text>
      </svg>
      <Tip tip={tip} />
    </>
  );
}
