import { useEffect, useRef } from "react";

import { useSticky } from "../hooks.js";
import { Icon } from "./ui.jsx";

/**
 * Terminal-style log viewer for a live evaluation.
 *
 * Lines are syntax-coloured on the way in: rupee figures green, percentages and
 * counts a paler green, PASS/FAIL in their semantic colours. It is a log, not a
 * table, so the colouring is there to let the eye find the money and the
 * verdicts while output is still streaming past.
 */

const PATTERNS = [
  [/\b(PASS|OK|saved|passed)\b/g, "log-ok"],
  [/\b(FAIL|FAILED|ERROR|CALIBRATION FAILED)\b/g, "log-bad"],
  [/\b(WARNING|warn|STUB!?)\b/gi, "log-warn"],
  [/Rs\s?[\d,]+(?:\.\d+)?/g, "log-money"],
  [/\$\d+(?:\.\d+)?/g, "log-money"],
  [/\b\d+(?:\.\d+)?%/g, "log-num"],
  [/\b[\d,]{2,}\b/g, "log-num"],
  [/\b(policy|engine|net|gross|direct|seed|model|cache)\b/g, "log-key"],
];

function colourise(line) {
  // Tokenise once, marking claimed spans, so an earlier pattern's match is
  // never re-matched by a later one (which would nest spans and mangle text).
  const marks = new Array(line.length).fill(null);
  for (const [re, cls] of PATTERNS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(line)) !== null) {
      if (m[0].length === 0) break;
      let free = true;
      for (let i = m.index; i < m.index + m[0].length; i++) if (marks[i]) free = false;
      if (free) for (let i = m.index; i < m.index + m[0].length; i++) marks[i] = cls;
    }
  }

  const out = [];
  let i = 0;
  while (i < line.length) {
    const cls = marks[i];
    let j = i;
    while (j < line.length && marks[j] === cls) j++;
    const text = line.slice(i, j);
    out.push(
      cls ? (
        <span key={i} className={cls}>
          {text}
        </span>
      ) : (
        text
      ),
    );
    i = j;
  }
  return out;
}

export default function Console({ log, running }) {
  const [open, setOpen] = useSticky("console-open", true);
  const bodyRef = useRef(null);
  const pinned = useRef(true);

  // Auto-scroll only while the user is already at the bottom, so scrolling
  // back to read something is not yanked away by the next line.
  useEffect(() => {
    const el = bodyRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [log, open]);

  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  if (!log.length) return null;

  return (
    <div className="console-card rise">
      <div
        className="console-head"
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen((v) => !v)}
      >
        <Icon.Chevron open={open} style={{ color: "var(--text-3)" }} />
        <span className="console-dots">
          <i style={{ background: "#f0576b" }} />
          <i style={{ background: "#f2a33c" }} />
          <i style={{ background: "#2fbf71" }} />
        </span>
        <span className="console-title">
          run_eval.py {running ? "· running" : "· finished"}
        </span>
        {running && (
          <span
            style={{
              marginLeft: "auto",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: "var(--fs-xs)",
              color: "var(--text-3)",
              fontFamily: "var(--mono)",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "var(--success)",
                animation: "pulse-dot 1.2s ease-in-out infinite",
              }}
            />
            live
          </span>
        )}
        {!running && (
          <span className="audit-count" style={{ marginLeft: "auto" }}>
            {log.length} lines
          </span>
        )}
      </div>

      {open && (
        <div className="console-body" ref={bodyRef} onScroll={onScroll}>
          {log.map((l, i) => (
            <div key={i} className={`ln${l.stream === "err" ? " log-err" : ""}`}>
              {l.stream === "err" ? l.line : colourise(l.line) }
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
