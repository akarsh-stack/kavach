import { useCallback, useEffect, useRef, useState } from "react";

import { evaluate, getRun, getSensitivity, waitForApi } from "./api.js";
import AuditTrail from "./components/AuditTrail.jsx";
import RecoveryConsole from "./components/RecoveryConsole.jsx";
import RunControls from "./components/RunControls.jsx";
import RunLog from "./components/RunLog.jsx";
import CostStack from "./components/CostStack.jsx";
import DiagnosisTable from "./components/DiagnosisTable.jsx";
import MetricCards from "./components/MetricCards.jsx";
import PolicyBars from "./components/PolicyBars.jsx";
import SensitivityGrid from "./components/SensitivityGrid.jsx";
import { Icon } from "./components/ui.jsx";


function Card({ title, note, children, className = "", delay = 1 }) {
  return (
    <section className={`card rise rise-${delay} ${className}`}>
      <div className="card-head">
        <h2>{title}</h2>
        {note && <p className="note">{note}</p>}
      </div>
      {children}
    </section>
  );
}

export default function App() {
  const [run, setRun] = useState(null);
  const [sens, setSens] = useState(null);
  const [error, setError] = useState("");
  const [runError, setRunError] = useState("");
  const lines = useRef([]);
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState([]);
  const [dismissed, setDismissed] = useState(false);
  const [tab, setTab] = useState("console");
  const [source, setSource] = useState("reference");

  // Default to the COMMITTED result, not the scratch one.
  //
  // `latest` is whatever ran most recently, including runs nobody meant to
  // start. Showing it by default meant a stray spawn could silently change what
  // the dashboard displays -- and it did, repeatedly. `reference` is the run in
  // git, so a fresh visitor always sees the numbers the README claims. We only
  // switch to `latest` after a run the user actually started.
  const load = useCallback(async (which = "reference") => {
    try {
      // Wait out the API/Vite startup race rather than reporting it as a
      // missing artifact -- see waitForApi in api.js.
      setRun(await waitForApi(() => getRun(which)));
      setSource(which);
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
    try {
      setSens(await waitForApi(() => getSensitivity()));
    } catch {
      setSens(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const start = ({ engine, limit }) => {
    setRunning(true);
    setLog([]);
    setRunError("");
    setDismissed(false);
    // The completion callback closes over stale state, and the log pane is
    // collapsible and sticky -- so "exit code 2" could be the only thing a
    // reader sees while the actual reason sat hidden one pane down.
    lines.current = [];
    evaluate(
      { limit, seed: 42, engine },
      (l) => {
        lines.current.push(l.line);
        setLog((prev) => [...prev, l]);
      },
      // The outcome used to be dropped on the floor: this callback took no
      // arguments, so a run that aborted -- a replay miss, a fatal model error,
      // or a 409 because another run was already going -- stopped the spinner
      // and loaded `latest` anyway, showing the PREVIOUS run's numbers as
      // though they were the new ones. The 409 case printed nothing at all,
      // because no process starts and the log pane hides itself when empty.
      async (result) => {
        setRunning(false);
        if (result && result.code !== 0) {
          const reason = lines.current
            .map((l) => (l || "").trim())
            .reverse()
            .find((l) => /^(Run aborted:|CALIBRATION FAILED|This batch has no recorded)/.test(l));
          setRunError(
            result.error ||
              reason ||
              `the run exited with code ${result.code}`,
          );
          return;
        }
        await load("latest");
      },
    );
  };

  const stubbed = run?.policies?.some((p) => p.is_stub);

  return (
    <div className="app">
      <header className="masthead rise">
        <div>
          <div className="eyebrow">
            <span className="dot" />
            Razorpay Buildathon · Track 03
          </div>
          <h1>Bounded revenue recovery</h1>
          <p className="sub">
            {run
              ? `${source} · ${run.workflow?.policy ?? "agent"} · ` +
                `${run.batch_size} payments · ${run.workflow?.engine ?? "?"} · ` +
                new Date(run.generated_at).toLocaleString()
              : "loading…"}
          </p>
        </div>

        <RunControls running={running} onRun={start} />

      </header>

      {error && (
        <div className="alert rise" style={{ "--tone": "var(--warning)" }}>
          <span className="alert-icon">
            <Icon.Alert />
          </span>
          <div className="alert-body">
            {/^API not reachable|Failed to fetch|NetworkError/i.test(error) ? (
              <>
                <strong>The API server is not answering</strong>
                {error}. It starts alongside this page via{" "}
                <code>npm run dev</code> — check that pane for a crash, then reload.
              </>
            ) : (
              <>
                <strong>No results yet</strong>
                {error}. Generate one with <code>python scripts/run_eval.py --no-llm</code>,
                or press <em>Run evaluation</em>.
              </>
            )}
          </div>
        </div>
      )}

      {runError && (
        <div className="alert rise" style={{ "--tone": "var(--danger)" }}>
          <span className="alert-icon">
            <Icon.Alert />
          </span>
          <div className="alert-body">
            <strong>That run did not finish</strong>
            {runError.replace(/\.\s*$/, "")}. The figures below are the previous result,
            unchanged — nothing from the failed run was loaded.
          </div>
        </div>
      )}

      {stubbed && !dismissed && (
        <div className="alert rise" style={{ "--tone": "var(--danger)" }}>
          <span className="alert-icon">
            <Icon.Alert />
          </span>
          <div className="alert-body">
            <strong>This run used the stub, not a model</strong>
            Policies tagged <em>stub</em> were decided by a deterministic heuristic with no
            model call. These numbers measure the plumbing, not decision quality, and must
            not be reported as a model result.
          </div>
          <button
            className="alert-close"
            onClick={() => setDismissed(true)}
            aria-label="Dismiss"
          >
            <Icon.Close />
          </button>
        </div>
      )}

      <RunLog log={log} running={running} failed={Boolean(runError)} />

      {run && (
        <>
          <div className="tabs rise">
            <button data-active={tab === "console"} onClick={() => setTab("console")}>
              Recovery console
            </button>
            <button data-active={tab === "evidence"} onClick={() => setTab("evidence")}>
              Evidence
            </button>
          </div>

          {tab === "console" && (
            <RecoveryConsole
              workflow={run.workflow}
              ledger={
                run.policies?.find((p) => p.policy === run.workflow.policy)?.ledger
              }
            />
          )}
        </>
      )}

      {run && tab === "evidence" && (
        <>
          <MetricCards run={run} />

          <div className="section">
            <Card
              title="Net recovery by policy"
              delay={5}
              note={
                <>
                  Both measures are rupees on one scale. The solid bar is net after
                  compliance exposure; the dashed outline is the direct figure a merchant
                  sees on their own P&amp;L. <em>Where the two diverge, that policy is
                  buying revenue with regulatory risk</em> — which is exactly why naive
                  retry loops are so common.
                </>
              }
            >
              <PolicyBars policies={run.policies} />
            </Card>
          </div>

          <div className="section">
            <Card
              title="Where the money went"
              delay={6}
              note={
                <>
                  Gross recovery is a vanity metric — any policy can win it by trying
                  everything on everything. What matters is what the trying cost. Click a
                  legend chip to isolate a cost.
                </>
              }
            >
              <CostStack policies={run.policies} />
            </Card>
          </div>

          <div className="section grid-2 wide-left">
            <Card
              title="Root cause diagnosis"
              delay={6}
              note={
                <>
                  Overall accuracy is near-meaningless: it is dominated by documented
                  reasons, where a lookup table scores ~100% by construction. The columns
                  that carry information are <em>held-out</em> reasons, absent from the
                  rulebook entirely, and <em>ambiguous</em> ones, where Razorpay documents
                  no gateway error code at all.
                </>
              }
            >
              <DiagnosisTable policies={run.policies} />
            </Card>

            <Card
              title="Does the conclusion survive our assumptions?"
              delay={6}
              note={
                <>
                  Each tile is one setting of the three softest numbers in the model, and
                  colour is the winning policy. <em>If a block changes colour, the result
                  depends on an assumption we cannot source</em> — better shown than
                  averaged away.
                </>
              }
            >
              <SensitivityGrid data={sens} />
            </Card>
          </div>

          <div className="section">
            <Card
              title="Audit trail"
              delay={6}
              note={
                <>
                  Defaulting to decisions that were <em>overruled</em>. “We retried 412
                  payments” does not answer a question from a risk team; “we declined to
                  retry these, and here is the rule that stopped each one” does.
                </>
              }
            >
              <AuditTrail audits={run.audits} policies={run.policies} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
