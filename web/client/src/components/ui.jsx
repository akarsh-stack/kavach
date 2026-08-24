/** Shared primitives: icons, tooltip, badges, empty state. */

const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" };

export const Icon = {
  Risk: (p) => (
    <svg viewBox="0 0 24 24" width="15" height="15" {...S} {...p}>
      <path d="M12 3 2.5 20h19L12 3Z" />
      <path d="M12 10v4M12 17.2v.1" />
    </svg>
  ),
  Wallet: (p) => (
    <svg viewBox="0 0 24 24" width="15" height="15" {...S} {...p}>
      <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H18a2 2 0 0 1 2 2v1.5" />
      <rect x="3" y="7.5" width="18" height="12" rx="2.5" />
      <path d="M16.5 13.5h.01" />
    </svg>
  ),
  Trend: (p) => (
    <svg viewBox="0 0 24 24" width="15" height="15" {...S} {...p}>
      <path d="M3 16.5 9 10l4 4 7.5-8" />
      <path d="M15.5 6h5v5" />
    </svg>
  ),
  Shield: (p) => (
    <svg viewBox="0 0 24 24" width="15" height="15" {...S} {...p}>
      <path d="M12 3 4.5 6v6c0 4.4 3.1 8.3 7.5 9.4 4.4-1.1 7.5-5 7.5-9.4V6L12 3Z" />
      <path d="m9.5 12 1.8 1.8 3.4-3.6" />
    </svg>
  ),
  Alert: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...S} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5v5M12 16.2v.1" />
    </svg>
  ),
  Close: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...S} {...p}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  ),
  Chevron: ({ open, ...p }) => (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      {...S}
      {...p}
      style={{
        transition: "transform .2s cubic-bezier(.16,1,.3,1)",
        transform: open ? "rotate(90deg)" : "none",
        ...(p.style || {}),
      }}
    >
      <path d="m9 6 6 6-6 6" />
    </svg>
  ),
  Empty: (p) => (
    <svg viewBox="0 0 24 24" width="26" height="26" {...S} {...p}>
      <rect x="3.5" y="5" width="17" height="14" rx="2.5" />
      <path d="M3.5 10h17M8.5 14h7" />
    </svg>
  ),
};

export function Tip({ tip }) {
  if (!tip) return null;
  // Flip away from the cursor near the edges so the tooltip never falls off a
  // laptop screen.
  const flipX = tip.x > window.innerWidth - 330;
  const flipY = tip.y > window.innerHeight - 190;
  return (
    <div
      className="tip"
      style={{
        left: flipX ? tip.x - 316 : tip.x + 16,
        top: flipY ? tip.y - 160 : tip.y + 16,
      }}
    >
      {tip.content}
    </div>
  );
}

export const TipTitle = ({ color, children }) => (
  <div className="tip-title">
    {color && (
      <span
        style={{ width: 8, height: 8, borderRadius: 3, background: color, flex: "none" }}
      />
    )}
    {children}
  </div>
);

export const TipRow = ({ k, v, accent }) => (
  <div className="tip-row">
    <span className="tip-k">{k}</span>
    <span className="tip-v" style={accent ? { color: accent } : undefined}>
      {v}
    </span>
  </div>
);

export const Badge = ({ tone = "neutral", children }) => (
  <span className={`badge badge-${tone}`}>{children}</span>
);

export const Empty = ({ children }) => (
  <div className="empty">
    <Icon.Empty />
    {children}
  </div>
);
