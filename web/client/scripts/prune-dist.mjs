/**
 * Remove local-only assets from the production bundle.
 *
 * `public/checkout.html` exists so `scripts/live_probe.py` can open a real
 * Razorpay checkout on localhost. Vite copies everything in `public/` verbatim,
 * so without this it also ships to the public deployment — a stray page with a
 * payment widget on it, reachable by anyone who guesses the path. It carries no
 * key (the key arrives as a query parameter), so this is tidiness rather than a
 * leak, but a deployed dashboard should not have a checkout page hanging off it.
 */
import { rmSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");

for (const name of ["checkout.html"]) {
  const target = join(dist, name);
  if (existsSync(target)) {
    rmSync(target);
    console.log(`  pruned   ${name}  (local-only)`);
  }
}
