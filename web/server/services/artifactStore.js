/**
 * Reads run artefacts from disk, with caching and conditional-GET support.
 *
 * `reference.json` is 2.2 MB and the dashboard fetches it on every load. The
 * original implementation did `JSON.parse(readFileSync(...))` per request,
 * which is roughly 30 ms of blocking CPU on the event loop each time -- fine
 * for one developer, visibly wrong under any concurrency, and it serialises
 * every other request behind it.
 *
 * So: cache the parsed object, key the cache on the file's mtime and size, and
 * hand out a strong ETag. A run started from the dashboard rewrites the file,
 * the stat changes, and the next read reparses. No invalidation to remember.
 *
 * The ETag matters more than the parse cache. A judge reloading the page gets
 * a 304 with an empty body instead of 2.2 MB, and that is the difference
 * between a dashboard that feels instant and one that does not.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { notFound } from "../lib/problem.js";

/** `latest` is scratch and may not exist; the committed run always does. */
const FALLBACK = { latest: "reference" };

export function createArtifactStore({ runsDir, metrics, logger }) {
  /** @type {Map<string, {mtimeMs:number,size:number,data:object,etag:string,bytes:number}>} */
  const cache = new Map();

  const fileFor = (name) => path.join(runsDir, `${name}.json`);

  /** Reject anything that could escape runsDir. */
  const assertSafeName = (name) => {
    if (!/^[a-z0-9_-]{1,64}$/i.test(name)) {
      throw notFound(`no run named ${JSON.stringify(name)}`, { name });
    }
  };

  function readRaw(name) {
    const file = fileFor(name);
    let stat;
    try {
      stat = fs.statSync(file);
    } catch {
      return null;
    }

    const hit = cache.get(name);
    if (hit && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) {
      metrics?.artifactCache.inc({ result: "hit" });
      return hit;
    }

    const started = process.hrtime.bigint();
    const text = fs.readFileSync(file, "utf-8");
    const data = JSON.parse(text);
    // Strong ETag over the bytes on disk. Cheap next to the parse we just did,
    // and it lets the client skip the whole payload on a reload.
    const etag = `"${crypto.createHash("sha1").update(text).digest("base64url")}"`;
    const entry = { mtimeMs: stat.mtimeMs, size: stat.size, data, etag, bytes: text.length };
    cache.set(name, entry);

    metrics?.artifactCache.inc({ result: "miss" });
    logger?.debug("artifact parsed", {
      name,
      bytes: text.length,
      ms: Number(process.hrtime.bigint() - started) / 1e6,
    });
    return entry;
  }

  return {
    /**
     * @returns {{data:object, etag:string, bytes:number}}
     * @throws {Problem} 404 when neither the run nor its fallback exists
     */
    get(name) {
      assertSafeName(name);
      const entry = readRaw(name) ?? (FALLBACK[name] ? readRaw(FALLBACK[name]) : null);
      if (!entry) throw notFound(`no run named ${JSON.stringify(name)}`, { name });
      return entry;
    },

    /** Names on disk, newest first. Used by /api/runs. */
    list() {
      if (!fs.existsSync(runsDir)) return [];
      return fs
        .readdirSync(runsDir)
        .filter((f) => f.endsWith(".json"))
        .map((f) => ({
          name: f.replace(/\.json$/, ""),
          modified: fs.statSync(path.join(runsDir, f)).mtime.toISOString(),
          bytes: fs.statSync(path.join(runsDir, f)).size,
        }))
        .sort((a, b) => b.modified.localeCompare(a.modified));
    },

    /** Called after a run writes, so the next read reparses immediately. */
    invalidate(name) {
      if (name) cache.delete(name);
      else cache.clear();
    },

    stats() {
      return { entries: cache.size, names: [...cache.keys()] };
    },
  };
}
