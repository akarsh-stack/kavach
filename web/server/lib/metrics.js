/**
 * A minimal Prometheus-compatible registry.
 *
 * Roughly eighty lines against a dependency, and the trade is deliberate: the
 * Python side of this project runs on pydantic and pytest alone, and the server
 * should show the same restraint. Counters and histograms are not hard; the
 * value is in choosing the right ones.
 *
 * What is measured here is what you would actually be paged about:
 *
 *   http_requests_total          rate and error ratio, by route and status
 *   http_request_duration_seconds latency distribution, not an average --
 *                                averages hide the tail that users notice
 *   evaluation_runs_total        how often a run is started, and how it ended
 *   evaluation_duration_seconds  how long runs take
 *   sse_clients                  currently attached streams; a leak here shows
 *                                as a number that only ever goes up
 *   artifact_cache               hit ratio for the 2 MB run artefact
 *
 * Route labels come from the route *pattern*, never the raw path. Labelling
 * with `/api/run/reference` and `/api/run/latest` separately is how a metrics
 * backend gets a cardinality explosion.
 */

const escapeLabel = (v) => String(v).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n");

const keyOf = (labels) =>
  Object.keys(labels)
    .sort()
    .map((k) => `${k}="${escapeLabel(labels[k])}"`)
    .join(",");

class Counter {
  constructor(name, help, labelNames = []) {
    Object.assign(this, { name, help, labelNames, type: "counter", values: new Map() });
  }
  inc(labels = {}, by = 1) {
    const k = keyOf(labels);
    this.values.set(k, (this.values.get(k) || 0) + by);
  }
  render() {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} counter`];
    for (const [k, v] of this.values) lines.push(`${this.name}${k ? `{${k}}` : ""} ${v}`);
    return lines;
  }
}

class Gauge {
  constructor(name, help) {
    Object.assign(this, { name, help, type: "gauge", value: 0 });
  }
  set(v) {
    this.value = v;
  }
  inc(by = 1) {
    this.value += by;
  }
  dec(by = 1) {
    this.value -= by;
  }
  render() {
    return [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} gauge`, `${this.name} ${this.value}`];
  }
}

class Histogram {
  constructor(name, help, buckets, labelNames = []) {
    Object.assign(this, { name, help, buckets, labelNames, type: "histogram", series: new Map() });
  }
  observe(labels, value) {
    const k = keyOf(labels);
    let s = this.series.get(k);
    if (!s) {
      s = { counts: new Array(this.buckets.length).fill(0), sum: 0, count: 0 };
      this.series.set(k, s);
    }
    s.sum += value;
    s.count += 1;
    for (let i = 0; i < this.buckets.length; i++) {
      if (value <= this.buckets[i]) s.counts[i] += 1;
    }
  }
  render() {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} histogram`];
    for (const [k, s] of this.series) {
      const base = k ? `{${k}` : "{";
      let cumulative = 0;
      for (let i = 0; i < this.buckets.length; i++) {
        cumulative = s.counts[i];
        lines.push(`${this.name}_bucket${base}${k ? "," : ""}le="${this.buckets[i]}"} ${cumulative}`);
      }
      lines.push(`${this.name}_bucket${base}${k ? "," : ""}le="+Inf"} ${s.count}`);
      lines.push(`${this.name}_sum${k ? `{${k}}` : ""} ${s.sum}`);
      lines.push(`${this.name}_count${k ? `{${k}}` : ""} ${s.count}`);
    }
    return lines;
  }
}

export function createMetrics() {
  const registry = [];
  const add = (m) => (registry.push(m), m);

  const metrics = {
    httpRequests: add(
      new Counter("http_requests_total", "HTTP requests by route, method and status", [
        "route",
        "method",
        "status",
      ]),
    ),
    httpDuration: add(
      new Histogram(
        "http_request_duration_seconds",
        "Request latency by route",
        [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
        ["route", "method"],
      ),
    ),
    evaluationRuns: add(
      new Counter("evaluation_runs_total", "Evaluation runs by terminal state", ["outcome"]),
    ),
    evaluationDuration: add(
      new Histogram(
        "evaluation_duration_seconds",
        "Wall-clock duration of an evaluation run",
        [1, 5, 15, 30, 60, 300, 900, 1800, 3600],
        ["outcome"],
      ),
    ),
    sseClients: add(new Gauge("sse_clients", "Currently attached log stream clients")),
    artifactCache: add(
      new Counter("artifact_cache_total", "Artifact reads by cache outcome", ["result"]),
    ),
    rateLimited: add(new Counter("rate_limited_total", "Requests rejected by the rate limiter")),

    render() {
      return registry.flatMap((m) => m.render()).join("\n") + "\n";
    },
  };
  return metrics;
}
