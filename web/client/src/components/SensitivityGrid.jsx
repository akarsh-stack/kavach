import { policyColor, rs } from "../api.js";
import { Tip, useTip } from "./Tip.jsx";

/**
 * Who wins at each point of the assumption grid.
 *
 * The cell value is an IDENTITY (which policy won), not a magnitude, so this is
 * a categorical encoding rather than a heatmap ramp. A sequential ramp here
 * would imply an ordering between policies that does not exist.
 *
 * This answers the question that decides whether the project is trustworthy: if
 * a column comes out a different colour, the conclusion depends on an
 * assumption we cannot source, and showing that is better than averaging it
 * away.
 */
const CELL = 46;
const GAPX = 4;
const ROWH = 34;

export default function SensitivityGrid({ data }) {
  const { tip, show, hide } = useTip();
  if (!data?.points?.length) return <div className="empty">No sensitivity sweep yet.</div>;

  const probs = [...new Set(data.points.map((p) => p.prob_scale))].sort((a, b) => a - b);
  const annoys = [...new Set(data.points.map((p) => p.annoyance_mult))].sort((a, b) => a - b);
  const expos = [...new Set(data.points.map((p) => p.exposure_mult))].sort((a, b) => a - b);
  const winners = [...new Set(data.points.map((p) => p.winner))];

  const at = (pr, an, ex) =>
    data.points.find(
      (p) => p.prob_scale === pr && p.annoyance_mult === an && p.exposure_mult === ex
    );

  const blockW = expos.length * (CELL + GAPX);
  const labelW = 96;
  const width = labelW + probs.length * (blockW + 34);
  const height = annoys.length * ROWH + 66;

  return (
    <>
      <div className="legend">
        {winners.map((w) => (
          <span className="item" key={w}>
            <span className="swatch" style={{ background: policyColor(w) }} />
            {w} wins
          </span>
        ))}
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto" }}>
        {probs.map((pr, bi) => {
          const x0 = labelW + bi * (blockW + 34);
          return (
            <g key={pr}>
              <text className="axis-label" x={x0} y={12}>
                recovery prob x{pr}
              </text>
              {expos.map((ex, ci) => (
                <text
                  key={ex}
                  className="axis-label"
                  x={x0 + ci * (CELL + GAPX) + CELL / 2}
                  y={30}
                  textAnchor="middle"
                  fontSize="10"
                >
                  x{ex}
                </text>
              ))}
              {annoys.map((an, ri) =>
                expos.map((ex, ci) => {
                  const pt = at(pr, an, ex);
                  if (!pt) return null;
                  const sorted = Object.entries(pt.nets).sort((a, b) => b[1] - a[1]);
                  const content = (
                    <>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>{pt.winner} wins</div>
                      <div className="k">
                        prob x{pt.prob_scale} / annoyance x{pt.annoyance_mult} / exposure x
                        {pt.exposure_mult}
                      </div>
                      <div style={{ marginTop: 6 }}>
                        {sorted.map(([k, v]) => (
                          <div key={k} className="v">
                            {k}: Rs {rs(v)}
                          </div>
                        ))}
                      </div>
                    </>
                  );
                  return (
                    <rect
                      key={`${an}-${ex}`}
                      x={x0 + ci * (CELL + GAPX)}
                      y={38 + ri * ROWH}
                      width={CELL}
                      height={ROWH - 4}
                      rx="4"
                      fill={policyColor(pt.winner)}
                      onMouseMove={(e) => show(e, content)}
                      onMouseLeave={hide}
                    />
                  );
                })
              )}
            </g>
          );
        })}

        {annoys.map((an, ri) => (
          <text
            key={an}
            className="axis-label"
            x={labelW - 10}
            y={38 + ri * ROWH + (ROWH - 4) / 2 + 4}
            textAnchor="end"
          >
            annoy x{an}
          </text>
        ))}
        <text className="axis-label" x={labelW} y={height - 8} fontSize="10">
          columns = compliance exposure multiplier
        </text>
      </svg>
      <Tip tip={tip} />
    </>
  );
}
