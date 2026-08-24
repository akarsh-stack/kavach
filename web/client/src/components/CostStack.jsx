import { useState } from "react";

import { policyLabel, rs } from "../api.js";
import { useGrowIn, useTip } from "../hooks.js";
import { Empty, Tip, TipRow, TipTitle } from "./ui.jsx";

/**
 * Where the money went, per policy.
 *
 * Six segments, assigned in fixed slot order and never cycled. A 2px surface
 * gap sits between adjacent fills so touching segments stay separable, and the
 * legend chips double as filters -- click one to isolate that cost, which is
 * the fastest way to answer "who is burning goodwill?".
 */
const PARTS = [
  { key: "mdr_paise", label: "Razorpay MDR", hex: "#5273e8" },
  { key: "attempt_cost_paise", label: "Retry attempts", hex: "#18a392" },
  { key: "message_cost_paise", label: "Customer messages", hex: "#8263e0" },
  { key: "escalation_cost_paise", label: "Human escalation", hex: "#d14a82" },
  { key: "goodwill", label: "Goodwill & churn", hex: "#c68325" },
  { key: "compliance_exposure_paise", label: "Compliance exposure", hex: "#2a9ac0" },
];

const ROW = 46;
const BAR = 20;
const LABEL_W = 176;
const VALUE_W = 100;
const GAP = 2;

export default function CostStack({ policies }) {
  const { tip, show, hide } = useTip();
  const [off, setOff] = useState(() => new Set());

  const active = PARTS.filter((p) => !off.has(p.key));

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
      const total = active.reduce((a, p2) => a + vals[p2.key], 0);
      const full = PARTS.reduce((a, p2) => a + vals[p2.key], 0);
      return { policy: p.policy, vals, total, full };
    })
    .filter((r) => r.full > 0)
    .sort((a, b) => b.total - a.total);

  const grow = useGrowIn(rows.map((r) => r.policy).join() + [...off].join());

  if (!rows.length) return <Empty>No costs incurred in this run.</Empty>;

  const width = 860;
  const plotW = width - LABEL_W - VALUE_W;
  const height = rows.length * ROW + 10;
  const max = Math.max(1, ...rows.map((r) => r.total));

  const toggle = (key) =>
    setOff((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      // Never let the user empty the chart entirely.
      return next.size === PARTS.length ? prev : next;
    });

  return (
    <>
      <div className="legend">
        {PARTS.map((p) => (
          <button
            key={p.key}
            className="legend-chip"
            data-off={off.has(p.key)}
            onClick={() => toggle(p.key)}
            title="Click to isolate or hide"
          >
            <span className="swatch" style={{ background: p.hex }} />
            {p.label}
          </button>
        ))}
      </div>

      <div className="chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img">
          <defs>
            {PARTS.map((p) => (
              <linearGradient key={p.key} id={`cs-${p.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={p.hex} stopOpacity="1" />
                <stop offset="100%" stopColor={p.hex} stopOpacity="0.74" />
              </linearGradient>
            ))}
          </defs>

          {rows.map((r, i) => {
            const y = i * ROW + 8;
            let cursor = LABEL_W;
            return (
              <g key={r.policy}>
                <text className="bar-label" x={0} y={y + 15}>
                  {policyLabel(r.policy)}
                </text>

                {active.map((part) => {
                  const v = r.vals[part.key];
                  if (v <= 0) return null;
                  const w = (v / max) * plotW * grow;
                  const x0 = cursor;
                  cursor += (v / max) * plotW + GAP;
                  const content = (
                    <>
                      <TipTitle color={part.hex}>{policyLabel(r.policy)}</TipTitle>
                      <TipRow k={part.label} v={`₹${rs(v)}`} accent={part.hex} />
                      <TipRow
                        k="Share of this policy"
                        v={`${((v / r.total) * 100).toFixed(1)}%`}
                      />
                      <TipRow k="Total cost" v={`₹${rs(r.total)}`} />
                    </>
                  );
                  return (
                    <rect
                      key={part.key}
                      className="bar-rect"
                      x={x0}
                      y={y}
                      width={Math.max(0, w - GAP)}
                      height={BAR}
                      rx="4"
                      fill={`url(#cs-${part.key})`}
                      style={{ transition: "width .7s cubic-bezier(.16,1,.3,1)" }}
                      onMouseMove={(e) => show(e, content)}
                      onMouseLeave={hide}
                    />
                  );
                })}

                <text className="bar-value" x={LABEL_W + plotW + 12} y={y + 15}>
                  ₹{rs(r.total)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <Tip tip={tip} />
    </>
  );
}
