/**
 * Copy the committed run artefacts into `public/` so the built site can serve
 * them without a backend.
 *
 * The dashboard normally talks to the Express API. That API spawns Python to
 * run an evaluation, which is fine on a laptop and impossible on a static host.
 * But everything a reader actually needs to SEE — the published run, the
 * sensitivity grid — is a committed JSON file that never changes at runtime.
 *
 * So the deployed build ships those two files as static assets and the client
 * falls back to them when no API answers. The run button is the only thing
 * lost, and it is the only thing that needed a server.
 */
import { copyFileSync, mkdirSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..", "..");
const out = join(here, "..", "public", "data");

const FILES = [
  ["data/runs/reference.json", "reference.json"],
  ["data/runs/sensitivity.json", "sensitivity.json"],
];

mkdirSync(out, { recursive: true });

let missing = 0;
for (const [from, to] of FILES) {
  const src = join(repo, from);
  if (!existsSync(src)) {
    console.error(`  MISSING  ${from}`);
    missing += 1;
    continue;
  }
  copyFileSync(src, join(out, to));
  console.log(`  staged   ${to}  ${(statSync(src).size / 1024).toFixed(0)} KB`);
}

if (missing) {
  // Better to fail the build than to deploy a dashboard whose every panel
  // renders an empty state.
  console.error(
    `\n${missing} artefact(s) missing — the deployed site would have no data.\n` +
      `Generate them first:  python scripts/run_eval.py --engine replay --limit 150 --save reference`,
  );
  process.exit(1);
}
