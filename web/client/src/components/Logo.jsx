/**
 * KAVACH — कवच, Sanskrit for armour.
 *
 * The mark is the argument of the project in one glyph: an angular shield
 * drawn as a single closed path, with the model's decision as a dot held
 * inside it. Nothing the model proposes leaves the boundary without passing
 * through it.
 *
 * Geometry is on a 24-unit grid with a flat top, shoulders at a third height
 * and a point at the base — an instrument's shield, not a heraldic one. It is
 * stroked rather than filled so it stays legible at 20px in the masthead and
 * at any size above.
 *
 * On mount the outline draws itself, then the dot arrives. That ordering is
 * the point: the boundary exists before the decision does.
 */
export default function Logo({ size = 34, animate = true, title = "Kavach" }) {
  return (
    <svg
      className={`logo${animate ? " logo-draw" : ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 26"
      fill="none"
      role="img"
      aria-label={title}
    >
      {/* Outer boundary. One continuous path so the draw reads as a single
          stroke rather than six separate edges appearing. */}
      <path
        className="logo-shield"
        d="M12 1.6 L21 5 V13.4 L12 24.4 L3 13.4 V5 Z"
        stroke="var(--accent-chrome)"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      {/* Inner rule: the policy layer sitting inside the boundary, not on it. */}
      <path
        className="logo-inner"
        d="M6.6 7.2 H17.4"
        stroke="var(--accent-chrome)"
        strokeWidth="1.1"
        strokeOpacity="0.45"
        strokeLinecap="round"
      />
      {/* The decision. */}
      <circle className="logo-dot" cx="12" cy="13.2" r="2.5" fill="var(--accent-chrome)" />
    </svg>
  );
}
