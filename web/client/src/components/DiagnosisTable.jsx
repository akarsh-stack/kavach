/**
 * Classification accuracy, split by difficulty.
 *
 * Overall accuracy is close to meaningless here: it is dominated by documented
 * reasons, where a lookup table scores ~100% by construction. The two columns
 * that carry information are held-out reasons (not in the rulebook at all) and
 * ambiguous ones (`payment_failed`, which Razorpay documents as carrying no
 * gateway error code). A model earns its place in those two columns or nowhere.
 */
export default function DiagnosisTable({ policies }) {
  const rows = policies.filter((p) => p.diagnosis.overall_n > 0);
  if (!rows.length) return <div className="empty">No diagnoses recorded.</div>;

  const pct = (v, n) => (n === 0 ? "—" : `${(v * 100).toFixed(1)}%`);

  return (
    <table>
      <thead>
        <tr>
          <th>policy</th>
          <th>overall</th>
          <th>n</th>
          <th>held-out</th>
          <th>n</th>
          <th>ambiguous</th>
          <th>n</th>
        </tr>
      </thead>
      <tbody>
        {[...rows]
          .sort((a, b) => b.diagnosis.overall - a.diagnosis.overall)
          .map((p) => (
            <tr key={p.policy}>
              <td className="name">
                {p.policy}
                {p.is_stub && <span className="pill bad" style={{ marginLeft: 8 }}>stub</span>}
              </td>
              <td>{pct(p.diagnosis.overall, p.diagnosis.overall_n)}</td>
              <td style={{ color: "var(--text-muted)" }}>{p.diagnosis.overall_n}</td>
              <td>{pct(p.diagnosis.heldout, p.diagnosis.heldout_n)}</td>
              <td style={{ color: "var(--text-muted)" }}>{p.diagnosis.heldout_n}</td>
              <td>{pct(p.diagnosis.ambiguous, p.diagnosis.ambiguous_n)}</td>
              <td style={{ color: "var(--text-muted)" }}>{p.diagnosis.ambiguous_n}</td>
            </tr>
          ))}
      </tbody>
    </table>
  );
}
