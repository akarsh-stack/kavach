import { useCallback, useEffect, useRef, useState } from "react";

import { evaluate, getRun, getSensitivity } from "./api.js";
import AuditTrail from "./components/AuditTrail.jsx";
import CostStack from "./components/CostStack.jsx";
import DiagnosisTable from "./components/DiagnosisTable.jsx";
import PolicyBars from "./components/PolicyBars.jsx";
import SensitivityGrid from "./components/SensitivityGrid.jsx";
import StatTiles from "./components/StatTiles.jsx";

export default function App() {
  const [run, setRun] = useState(null);
  const [sens, setSens] = useState(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState([]);
  const [engine, setEngine] = useState("none");
  const [limit, setLimit] = useState(300);
  const consoleRef = useRef(null);

  const load = useCallback(async () => {
    try {
      setRun(await getRun("latest"));
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
    try {
      setSens(await getSensitivity());
    } catch {
      setSens(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (consoleRef.current) consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [log]);

  const start = () => {
    setRunning(true);
    setLog([]);
    evaluate(
      { limit, seed: 42, engine },
      (l) => setLog((prev) => [...prev, l]),
      async () => {
        setRunning(false);
        await load();
      }
    );
  };

  const stubbed = run?.policies?.some((p) => p.is_stub);

  return (
    <div className="app">
      <div className="masthead">
        <div>
          <h1>Revenue Recovery — bounded agent vs four alternatives</h1>
          <p className="sub">
            {run
              ? `${run.batch_size} failed Razorpay payments · seed ${run.seed} · ${new Date(
                  run.generated_at
                ).toLocaleString()}`
              : "loading…"}
          </p>
        </div>
        <div className="controls">
          <select value={engine} onChange={(e) => setEngine(e.target.value)} disabled={running}>
            <option value="none">no model (3 baselines)</option>
            <option value="ollama">ollama (local)</option>
            <option value="anthropic">anthropic</option>
            <option value="stub">stub</option>
          </select>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            disabled={running}
          >
            {[100, 150, 300, 600].map((n) => (
              <option key={n} value={n}>
                {n} failures
              </option>
            ))}
          </select>
          <button className="primary" onClick={start} disabled={running}>
            {running ? "running…" : "run evaluation"}
          </button>
        </div>
      </div>

      {error && (
        <div className="banner">
          <strong>No results yet.</strong> {error}
          <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
            Generate one with <code>python scripts/run_eval.py --no-llm</code>, or press
            “run evaluation”.
          </div>
        </div>
      )}

      {stubbed && (
        <div className="banner">
          <strong>This run used the stub, not a model.</strong> The policies marked{" "}
          <span className="pill bad">stub</span> were decided by a deterministic heuristic with
          no model call. These numbers measure the plumbing, not decision quality, and must not
          be reported as a model result.
        </div>
      )}

      {log.length > 0 && (
        <div className="card">
          <h2>run log</h2>
          <div className="console" ref={consoleRef}>
            {log.map((l, i) => (
              <div key={i} className={l.stream === "err" ? "err" : ""}>
                {l.line}
              </div>
            ))}
          </div>
        </div>
      )}

      {run && (
        <>
          <StatTiles run={run} />

          <div className="card">
            <h2>Net recovery by policy</h2>
            <p className="note">
              Both bars are rupees on one scale. The solid bar is net after compliance exposure;
              the dashed outline is the direct figure a merchant sees on their own P&amp;L. Where
              the two diverge, that policy is buying its revenue with regulatory risk — which is
              exactly why naive retry loops are so common. Policies marked{" "}
              <span style={{ color: "var(--warning)" }}>*</span> run without guardrails by design.
            </p>
            <PolicyBars policies={run.policies} />
          </div>

          <div className="card">
            <h2>Where the money went</h2>
            <p className="note">
              Gross recovery is a vanity metric: any policy can win it by trying everything on
              everything. What matters is what the trying cost.
            </p>
            <CostStack policies={run.policies} />
          </div>

          <div className="grid2">
            <div className="card">
              <h2>Root cause diagnosis</h2>
              <p className="note">
                Overall accuracy is near-meaningless — it is dominated by documented reasons,
                where a lookup table scores ~100% by construction. The columns that carry
                information are held-out reasons (absent from the rulebook entirely) and
                ambiguous ones, where Razorpay documents no gateway error code at all.
              </p>
              <DiagnosisTable policies={run.policies} />
            </div>

            <div className="card">
              <h2>Does the conclusion survive our assumptions?</h2>
              <p className="note">
                Each cell is one setting of the three softest numbers in the model. Colour is the
                winning policy. If a block changes colour, the result depends on an assumption we
                cannot source — and we would rather show that than average it away.
              </p>
              <SensitivityGrid data={sens} />
            </div>
          </div>

          <div className="card">
            <h2>Audit trail</h2>
            <p className="note">
              Defaulting to the decisions that were <em>overruled</em>. “We retried 412 payments”
              does not answer a question from a risk team; “we declined to retry these, and here
              is the rule that stopped each one” does.
            </p>
            <AuditTrail audits={run.audits} />
          </div>
        </>
      )}
    </div>
  );
}
