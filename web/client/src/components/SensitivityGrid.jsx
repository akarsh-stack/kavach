import { useState } from "react";

import { policyColor, policyHex, policyLabel, rs } from "../api.js";
import { useTip } from "../hooks.js";
import { Empty, Tip, TipRow, TipTitle } from "./ui.jsx";

/**
 * Who wins at each point of the assumption grid.
 *
 * The cell value is an IDENTITY (which policy won), not a magnitude, so this is
 * a categorical encoding rather than a heatmap ramp. A sequential ramp here
 * would imply an ordering between policies that does not exist.
 *
 * This answers the question that decides whether the project is trustworthy: if
 * a block comes out a different colour, the conclusion depends on an assumption
 * we cannot source.
 */
const CELL = 42;
const GAP = 5;
const ROWH = 38;
const LABEL_W = 74;
const BLOCK_GAP = 30;

export default function SensitivityGrid({ data }) {
  const { tip, show, hide } = useTip();
  const [focus, setFocus] = useState(null);

  if (!data?.points?.length) return <Empty>No sensitivity sweep recorded yet.</Empty>;

  const probs = [...new Set(data.points.map((p) => p.prob_scale))].sort((a, b) => a - b);
  const annoys = [...new Set(data.points.map((p) => p.annoyance_mult))].sort((a, b) => a - b);
  const expos = [...new Set(data.points.map((p) => p.exposure_mult))].sort((a, b) => a - b);
  const winners = [...new Set(data.points.map((p) => p.winner))];

  const at = (pr, an, ex) =>
    data.points.find(
      (p) => p.prob_scale === pr && p.annoyance_mult === an && p.exposure_mult === ex,
    );

  const blockW = expos.length * (CELL + GAP) - GAP;
  const width = LABEL_W + probs.length * (blockW + BLOCK_GAP);
  const height = annoys.length * ROWH + 74;

  const counts = winners.map((w) => ({
    w,
    n: data.points.filter((p) => p.winner === w).length,
  }));

  return (
    <>
      <div className="legend">
        {counts.map(({ w, n }) => (
          <button
            key={w}
            className="legend-chip"
            data-off={focus !== null && focus !== w}
            onClick={() => setFocus((f) => (f === w ? null : w))}
          >
            <span className="swatch" style={{ background: policyColor(w) }} />
            {policyLabel(w)} wins {n}/{data.points.length}
          </button>
        ))}
      </div>

      <div className="chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img">
          {probs.map((pr, bi) => {
            const x0 = LABEL_W + bi * (blockW + BLOCK_GAP);
            return (
              <g key={pr}>
                <text className="heat-block-title" x={x0} y={11} fill="var(--text-3)">
                  RECOVERY PROB ×{pr}
                </text>

                {expos.map((ex, ci) => (
                  <text
                    key={ex}
                    className="axis-text"
                    x={x0 + ci * (CELL + GAP) + CELL / 2}
                    y={30}
                    textAnchor="middle"
                  >
                    ×{ex}
                  </text>
                ))}

                {annoys.map((an, ri) =>
                  expos.map((ex, ci) => {
                    const pt = at(pr, an, ex);
                    if (!pt) return null;
                    const dimmed = focus !== null && focus !== pt.winner;
                    const sorted = Object.entries(pt.nets).sort((a, b) => b[1] - a[1]);
                    const content = (
                      <>
                        <TipTitle color={policyColor(pt.winner)}>
                          {policyLabel(pt.winner)} wins
                        </TipTitle>
                        <TipRow k="Recovery prob" v={`×${pt.prob_scale}`} />
                        <TipRow k="Annoyance cost" v={`×${pt.annoyance_mult}`} />
                        <TipRow k="Compliance exposure" v={`×${pt.exposure_mult}`} />
                        <div
                          style={{
                            marginTop: 8,
                            paddingTop: 8,
                            borderTop: "1px solid var(--border)",
                          }}
                        >
                          {sorted.map(([k, v]) => (
                            <TipRow
                              key={k}
                              k={policyLabel(k)}
                              v={`₹${rs(v)}`}
                              accent={k === pt.winner ? policyHex(k) : undefined}
                            />
                          ))}
                        </div>
                      </>
                    );
                    return (
                      <rect
                        key={`${an}-${ex}`}
                        className="heat-cell"
                        x={x0 + ci * (CELL + GAP)}
                        y={38 + ri * ROWH}
                        width={CELL}
                        height={ROWH - GAP}
                        rx="7"
                        fill={policyColor(pt.winner)}
                        opacity={dimmed ? 0.16 : 0.92}
                        onMouseMove={(e) => show(e, content)}
                        onMouseLeave={hide}
                      />
                    );
                  }),
                )}
              </g>
            );
          })}

          {annoys.map((an, ri) => (
            <text
              key={an}
              className="axis-text"
              x={LABEL_W - 12}
              y={38 + ri * ROWH + (ROWH - GAP) / 2 + 3.5}
              textAnchor="end"
            >
              annoy ×{an}
            </text>
          ))}

          <text className="axis-text" x={LABEL_W} y={height - 10}>
            columns = compliance exposure multiplier · rows = annoyance cost multiplier
          </text>
        </svg>
      </div>
      <Tip tip={tip} />
    </>
  );
}
