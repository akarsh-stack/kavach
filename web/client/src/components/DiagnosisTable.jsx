import { policyColor, policyLabel } from "../api.js";
import { Badge, Empty } from "./ui.jsx";

/**
 * Classification accuracy, split by difficulty.
 *
 * Overall accuracy is close to meaningless here: it is dominated by documented
 * reasons, where a lookup table scores ~100% by construction. The two columns
 * that carry information are held-out reasons (absent from the rulebook
 * entirely) and ambiguous ones, where Razorpay documents no gateway error code
 * at all. Those are visually promoted; overall is deliberately muted.
 */
export default function DiagnosisTable({ policies }) {
  const rows = policies.filter((p) => p.diagnosis.overall_n > 0);
  if (!rows.length) return <Empty>No diagnoses recorded in this run.</Empty>;

  const cell = (v, n, strong) => {
    if (!n) return <span className="muted">—</span>;
    return (
      <span className={strong ? "cell-strong" : undefined}>{(v * 100).toFixed(1)}%</span>
    );
  };

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Policy</th>
            <th>Overall</th>
            <th>n</th>
            <th>Held-out</th>
            <th>n</th>
            <th>Ambiguous</th>
            <th>n</th>
          </tr>
        </thead>
        <tbody>
          {[...rows]
            .sort((a, b) => b.diagnosis.overall - a.diagnosis.overall)
            .map((p) => (
              <tr key={p.policy}>
                <td>
                  <span className="name" style={{ display: "flex" }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 3,
                        background: policyColor(p.policy),
                        flex: "none",
                      }}
                    />
                    {policyLabel(p.policy)}
                    {p.is_stub && <Badge tone="danger">stub</Badge>}
                  </span>
                </td>
                <td className="muted">{cell(p.diagnosis.overall, p.diagnosis.overall_n)}</td>
                <td className="muted">{p.diagnosis.overall_n}</td>
                <td>{cell(p.diagnosis.heldout, p.diagnosis.heldout_n, true)}</td>
                <td className="muted">{p.diagnosis.heldout_n}</td>
                <td>{cell(p.diagnosis.ambiguous, p.diagnosis.ambiguous_n, true)}</td>
                <td className="muted">{p.diagnosis.ambiguous_n}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
