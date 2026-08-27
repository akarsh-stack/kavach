import { useEffect, useState } from "react";

import { getCapabilities, waitForApi } from "../api.js";

/**
 * The run toolbar, constrained to what this machine can actually do.
 *
 * Previously it offered every engine unconditionally. Choosing one without a
 * key fell through to replay, and replay only covers the recorded batch size —
 * so "Groq" at 300 payments produced a Python traceback in the log pane. A
 * judge clicking a dropdown must not be able to reach that.
 *
 * So the server reports which providers have credentials and what the cache
 * covers, and this component:
 *
 *   - disables engines with no key, and says which key is missing
 *   - pins the batch size when the run will be replayed, because any other
 *     size is a guaranteed cache miss
 *   - defaults to a combination that works
 */
export default function RunControls({ running, onRun }) {
  const [caps, setCaps] = useState(null);
  const [engine, setEngine] = useState("");
  const [limit, setLimit] = useState(150);

  useEffect(() => {
    // Losing the startup race here is worse than it looks: the catch below
    // pins the dropdown to "No model" for the session, so a machine with a
    // working Ollama key would be told it has none.
    waitForApi(() => getCapabilities())
      .then((c) => {
        setCaps(c);
        // Prefer a live model, then the recorded run, then baselines. Never
        // land on something that cannot run.
        const first = ["ollama", "gemini", "groq", "anthropic", "replay", "none"].find(
          (id) => c.engines.find((e) => e.id === id)?.available,
        );
        setEngine(first || "none");
        if (first === "replay" && c.cache?.limit) setLimit(c.cache.limit);
      })
      .catch(() => setCaps({ engines: [{ id: "none", label: "No model", available: true }] }));
  }, []);

  if (!caps) {
    return <div className="toolbar" style={{ opacity: 0.5 }}>loading…</div>;
  }

  // Deployed with no backend. Offering a disabled dropdown and a dead button
  // would read as broken; saying what this is reads as deliberate.
  if (caps.static) {
    return (
      <div className="static-note">
        <span className="static-badge">Published run</span>
        <p>
          Read-only deployment — the figures below are the committed evaluation.
          Running a fresh one needs Python, so{" "}
          <a
            href="https://github.com/akarsh-stack/kavach#quickstart"
            target="_blank"
            rel="noreferrer"
          >
            clone the repo
          </a>{" "}
          to do that. No API key required there either.
        </p>
      </div>
    );
  }

  const isReplay = engine === "replay";
  const cachedLimit = caps.cache?.limit ?? 150;
  const sizes = isReplay ? [cachedLimit] : [100, 150, 300, 600];

  const pick = (id) => {
    setEngine(id);
    if (id === "replay") setLimit(cachedLimit);
  };

  return (
    <div>
      <div className="toolbar">
        <span className="select">
          <select
            value={engine}
            onChange={(e) => pick(e.target.value)}
            disabled={running}
            aria-label="Decision engine"
          >
            {caps.engines.map((e) => (
              <option key={e.id} value={e.id} disabled={!e.available}>
                {e.available ? e.label : `${e.label} — ${e.why || "unavailable"}`}
              </option>
            ))}
          </select>
        </span>

        <span className="select">
          <select
            value={limit}
            onChange={(ev) => setLimit(Number(ev.target.value))}
            disabled={running || isReplay}
            aria-label="Batch size"
            title={isReplay ? `The recorded run covers ${cachedLimit} payments` : undefined}
          >
            {sizes.map((n) => (
              <option key={n} value={n}>
                {n} failures
              </option>
            ))}
          </select>
        </span>

        <button className="btn btn-primary" onClick={() => onRun({ engine, limit })} disabled={running}>
          {running && <span className="spinner" />}
          {running ? "Running…" : "Run evaluation"}
        </button>
      </div>

      {isReplay && (
        <p className="toolbar-note">
          Replaying {caps.cache.entries.toLocaleString()} recorded decisions from{" "}
          <code>{caps.cache.model}</code>. Batch size is fixed at {cachedLimit} — that is
          what the committed cache covers, and any other size would miss.
        </p>
      )}
      {!isReplay && engine !== "none" && (
        <p className="toolbar-note">
          Live run against <code>{engine}</code>. New decisions are recorded to the cache.
        </p>
      )}
    </div>
  );
}
