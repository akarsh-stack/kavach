import { useCallback, useState } from "react";

/** Shared hover tooltip. Every mark in this dashboard gets one. */
export function useTip() {
  const [tip, setTip] = useState(null);
  const show = useCallback((e, content) => {
    setTip({ x: e.clientX, y: e.clientY, content });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  return { tip, show, hide };
}

export function Tip({ tip }) {
  if (!tip) return null;
  // Flip away from the cursor near the right/bottom edges so the tooltip never
  // falls off screen on a laptop display.
  const flipX = tip.x > window.innerWidth - 300;
  const flipY = tip.y > window.innerHeight - 160;
  return (
    <div
      className="tip"
      style={{
        left: flipX ? tip.x - 292 : tip.x + 14,
        top: flipY ? tip.y - 130 : tip.y + 14,
      }}
    >
      {tip.content}
    </div>
  );
}
