import { useState } from "react";

import { policyHex, policyLabel, rs } from "../api.js";
import { useGrowIn, useTip } from "../hooks.js";
import { Tip, TipRow, TipTitle } from "./ui.jsx";

const ROW = 52;
const BAR = 16;
const LABEL_W = 176;
const VALUE_W = 108;

/**
 * Net vs direct recovery per policy.
 *
 * Two measures on ONE axis -- both are rupees, so they share a scale. Never a
 * second y-axis.
 *
 * The gap between them is the argument of the whole project: `direct` is what a
 * merchant sees on their own P&L, `net` is what remains once re-presenting risk
 * declines is priced. A policy whose dashed outline runs past its solid bar is
 * buying revenue with regulatory exposure.
 */
export default function PolicyBars({ policies }) {
  const { tip, show, hide } = useTip();
  const [hovered, setHovered] = useState(null);
  const [showDirect, setShowDirect] = useState(true);

  const rows = [...policies].sort((a, b) => b.ledger.net_paise - a.ledger.net_paise);
  const grow = useGrowIn(rows.map((r) => r.policy).join() + showDirect);

  const width = 860;
  const plotW = width - LABEL_W - VALUE_W;
  const height = rows.length * ROW + 26;

  const max = Math.max(
    1,
    ...rows.map((p) => Math.max(p.ledger.net_paise, p.ledger.net_direct_paise)),
  );
  const x = (v) => Math.max(0, (v / max) * plotW) * grow;

  return (
    <>
      <div className="legend">
        <button
          className="legend-chip"
          data-off={!showDirect}
          onClick={() => setShowDirect((v) => !v)}
          title="Toggle the direct-P&L reference"
        >
          <span
            className="swatch"
            style={{ background: "transparent", border: "1.5px dashed var(--text-3)" }}
          />
          Direct (merchant P&amp;L)
        </button>
        <span className="legend-chip" style={{ cursor: "default" }}>
          <span
            className="swatch"
            style={{ background: "linear-gradient(90deg,var(--series-1),var(--series-3))" }}
          />
          Net (after compliance exposure)
        </span>
      </div>

      <div className="chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img">
          <defs>
            {rows.map((p) => {
              const c = policyHex(p.policy);
              return (
                <linearGradient key={p.policy} id={`g-${p.policy}`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor={c} stopOpacity="0.72" />
                  <stop offset="100%" stopColor={c} stopOpacity="1" />
                </linearGradient>
              );
            })}
          </defs>

          {[0.25, 0.5, 0.75, 1].map((f) => (
            <line
              key={f}
              className="grid-line"
              x1={LABEL_W + f * plotW}
              x2={LABEL_W + f * plotW}
              y1={4}
              y2={rows.length * ROW}
            />
          ))}

          {rows.map((p, i) => {
            const y = i * ROW + 10;
            const c = policyHex(p.policy);
            const net = p.ledger.net_paise;
            const direct = p.ledger.net_direct_paise;
            const diverges = direct - net > max * 0.008;
            const dim = hovered !== null && hovered !== p.policy;

            const content = (
              <>
                <TipTitle color={c}>{policyLabel(p.policy)}</TipTitle>
                <TipRow k="Net (after exposure)" v={`₹${rs(net)}`} accent={c} />
                <TipRow k="Direct (P&L only)" v={`₹${rs(direct)}`} />
                <TipRow k="Gross recovered" v={`₹${rs(p.ledger.gross_recovered_paise)}`} />
                <TipRow k="Total spend" v={`₹${rs(p.ledger.spend_paise)}`} />
                {p.ledger.policy_violations > 0 && (
                  <TipRow
                    k="Policy violations"
                    v={p.ledger.policy_violations}
                    accent="var(--danger)"
                  />
                )}
              </>
            );

            return (
              <g
                key={p.policy}
                className="bar-row"
                data-dim={dim}
                onMouseMove={(e) => show(e, content)}
                onMouseEnter={() => setHovered(p.policy)}
                onMouseLeave={() => {
                  setHovered(null);
                  hide();
                }}
              >
                <rect x={0} y={y - 10} width={width} height={ROW} fill="transparent" />

                <text className="bar-label" x={0} y={y + 12}>
                  {policyLabel(p.policy)}
                </text>
                {!p.enforce_guardrails && (
                  <text className="axis-text" x={0} y={y + 26} fill="var(--warning)">
                    no guardrails
                  </text>
                )}

                {showDirect && diverges && (
                  <rect
                    x={LABEL_W}
                    y={y - 3}
                    width={x(direct)}
                    height={BAR + 6}
                    rx="6"
                    fill="none"
                    stroke="var(--text-4)"
                    strokeWidth="1"
                    strokeDasharray="4 3"
                    style={{ transition: "width .8s cubic-bezier(.16,1,.3,1)" }}
                  />
                )}

                <rect
                  className="bar-rect"
                  x={LABEL_W}
                  y={y}
                  width={x(net)}
                  height={BAR}
                  rx={BAR / 2}
                  fill={`url(#g-${p.policy})`}
                  style={{ transition: "width .8s cubic-bezier(.16,1,.3,1)" }}
                />

                <text className="bar-value" x={LABEL_W + plotW + 12} y={y + 13}>
                  ₹{rs(net)}
                </text>
              </g>
            );
          })}

          <line
            className="axis-line"
            x1={LABEL_W}
            x2={LABEL_W}
            y1={4}
            y2={rows.length * ROW}
          />
          <text className="axis-text" x={LABEL_W} y={rows.length * ROW + 18}>
            ₹0
          </text>
          <text
            className="axis-text"
            x={LABEL_W + plotW}
            y={rows.length * ROW + 18}
            textAnchor="end"
          >
            ₹{rs(max)}
          </text>
        </svg>
      </div>
      <Tip tip={tip} />
    </>
  );
}
