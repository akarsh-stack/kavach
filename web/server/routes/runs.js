/**
 * Read-only access to run artefacts.
 *
 * Conditional GET is the point of this file. `reference.json` is 2.2 MB and is
 * fetched on every page load; an `If-None-Match` that matches returns 304 with
 * no body. Express will do this for you via `res.json` + `etag`, but only after
 * serialising the whole object to compute the hash -- which is exactly the work
 * we are trying to avoid. The store already holds a strong ETag computed once
 * per file change, so the check happens before any serialisation.
 */

import { Router } from "express";

import { badRequest } from "../lib/problem.js";

export function runsRouter({ artifacts, capabilities }) {
  const router = Router();

  /** Send `data` with cache validators, honouring If-None-Match. */
  const sendCached = (req, res, entry) => {
    res.set("ETag", entry.etag);
    // Artefacts change only when a run writes one. `must-revalidate` keeps the
    // client honest while still letting the 304 do the work.
    res.set("Cache-Control", "no-cache, must-revalidate");
    if (req.get("if-none-match") === entry.etag) {
      res.status(304).end();
      return;
    }
    res.json(entry.data);
  };

  router.get("/runs", (req, res) => {
    res.json({ runs: artifacts.list() });
  });

  router.get("/run/:name", (req, res) => {
    sendCached(req, res, artifacts.get(req.params.name));
  });

  router.get("/sensitivity", (req, res) => {
    sendCached(req, res, artifacts.get("sensitivity"));
  });

  router.get("/capabilities", (req, res) => {
    res.set("Cache-Control", "no-store");
    res.json(capabilities());
  });

  // Kept so a stray old client gets a clear answer rather than a 404 it has to
  // interpret. Cheap, and removing endpoints is how you break other people.
  router.get("/run", () => {
    throw badRequest("name a run: /api/run/reference or /api/run/latest");
  });

  return router;
}
