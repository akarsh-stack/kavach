import { useCallback, useEffect, useRef, useState } from "react";

const REDUCED =
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Animate a number from 0 to `target` on mount and on change.
 *
 * rAF rather than a CSS transition because the value is text, and eased with
 * a cubic ease-out so it decelerates into the final figure instead of
 * stopping dead. Honours prefers-reduced-motion by jumping straight there.
 */
export function useCountUp(target, duration = 900) {
  const [value, setValue] = useState(REDUCED ? target : 0);
  const frame = useRef();
  const from = useRef(0);

  useEffect(() => {
    if (REDUCED || !Number.isFinite(target)) {
      setValue(target);
      return;
    }
    const start = performance.now();
    const origin = from.current;
    const delta = target - origin;

    const tick = (now) => {
      // Clamp BOTH ends. requestAnimationFrame hands back the timestamp of the
      // frame it belongs to, and that can predate the performance.now() taken
      // inside this effect during the very same frame -- so `now - start` goes
      // negative, and with only an upper clamp so does the eased fraction.
      // 1 - (1-t)^3 at t = -0.02 is about -0.06, which rendered the headline
      // figures as small NEGATIVE amounts of money for a frame or two on mount.
      // Caught on the Evidence tab reading "VALUE AT RISK -4,414".
      const t = Math.min(1, Math.max(0, (now - start) / duration));
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(origin + delta * eased);
      if (t < 1) frame.current = requestAnimationFrame(tick);
      else from.current = target;
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [target, duration]);

  return value;
}

/**
 * Drives bar/segment entrance animations.
 *
 * Returns 0 on the first paint then 1 immediately after, so widths can be
 * interpolated by CSS transition without every chart needing its own timer.
 * `key` restarts it, which is what makes bars re-grow when the data changes.
 */
export function useGrowIn(key) {
  const [grown, setGrown] = useState(REDUCED);
  useEffect(() => {
    if (REDUCED) return;
    setGrown(false);
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setGrown(true)));
    return () => cancelAnimationFrame(id);
  }, [key]);
  return grown ? 1 : 0;
}

/** Shared hover tooltip state. */
export function useTip() {
  const [tip, setTip] = useState(null);
  const show = useCallback((e, content) => {
    setTip({ x: e.clientX, y: e.clientY, content });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  return { tip, show, hide };
}

/** Persisted boolean, so a collapsed console stays collapsed across reloads. */
export function useSticky(key, initial) {
  const [v, setV] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : JSON.parse(raw);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(v));
    } catch {
      /* private mode; not worth failing over */
    }
  }, [key, v]);
  return [v, setV];
}
