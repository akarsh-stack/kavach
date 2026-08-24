"""Getting reliable JSON out of a model that will not honour a schema.

Every provider claims structured output and each means something different by
it. Measured, not assumed:

  Anthropic       `output_config.format` genuinely constrains decoding
  Ollama (local)  `format` genuinely constrains decoding
  Ollama Cloud    accepts the schema and ignores it -- probed directly, it
                  returned well-formed JSON with invented keys and
                  `confidence: "high"` as a string
  Gemini          `responseSchema` works, but rejects `additionalProperties`
  Groq            `json_object` guarantees valid JSON, not the right shape

So the contract goes in the prompt for everyone, and every response is
validated with a repair turn. One hardened path is easier to trust than five
provider-specific ones, and it costs nothing where decoding is already
constrained.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError


def contract(schema_cls: type[BaseModel], schema: dict) -> str:
    """Render the required JSON shape as prose the model will actually follow.

    Generated from the Pydantic schema rather than hand-written, so it cannot
    drift out of sync with what the validator accepts -- a prompt describing a
    slightly different contract from the one being enforced is a very annoying
    bug to find.
    """
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    defs = schema.get("$defs", {})

    def describe(spec: dict) -> str:
        if "enum" in spec:
            return " | ".join(json.dumps(v) for v in spec["enum"])
        if "$ref" in spec:
            return describe(defs.get(spec["$ref"].split("/")[-1], {}))
        if "anyOf" in spec:
            return " | ".join(describe(s) for s in spec["anyOf"])
        t = spec.get("type")
        if t in ("number", "integer"):
            lo, hi = spec.get("minimum"), spec.get("maximum")
            return f"number between {lo} and {hi}" if lo is not None and hi is not None else "number"
        if t == "string":
            n = spec.get("maxLength")
            return f"string (max {n} chars)" if n else "string"
        if t == "null":
            return "null"
        return t or "value"

    lines = ["# Required output format", "", "Return ONLY this JSON object:", "{"]
    for name, spec in props.items():
        opt = "" if name in required else "   (optional)"
        lines.append(f'  "{name}": {describe(spec)},{opt}')
    lines.append("}")
    lines += [
        "",
        "Every key above is required unless marked optional. Do not add keys. Do not",
        "rename them. Do not wrap the object in markdown fences or explanatory text.",
        '`confidence` is a NUMBER between 0 and 1, not a word like "high".',
    ]
    return "\n".join(lines)


def errors(exc: ValidationError) -> str:
    out = []
    for e in exc.errors()[:6]:
        loc = ".".join(str(p) for p in e["loc"]) or "(root)"
        out.append(f"  - {loc}: {e['msg']}")
    return "\n".join(out)


def extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may be wrapped in prose.

    Models without constrained decoding fence output in ```json blocks or add a
    sentence before it often enough to be worth handling here rather than
    burning a repair turn on it.
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s[3:]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    start, end = s.find("{"), s.rfind("}")
    return s[start : end + 1] if start != -1 and end > start else s


def repair_turn(text: str, exc: ValidationError) -> list[dict]:
    """Follow-up messages handing the model its own output and the exact errors.

    Far more reliable than resending the same prompt and hoping: the model can
    see precisely which key it invented or which type it got wrong.
    """
    return [
        {"role": "assistant", "content": text},
        {
            "role": "user",
            "content": (
                "That did not match the required shape:\n"
                f"{errors(exc)}\n\n"
                "Return ONLY the corrected JSON object with exactly the required "
                "keys. No prose, no extra keys."
            ),
        },
    ]


def gemini_schema(schema: dict) -> dict:
    """Strip what Gemini's responseSchema rejects.

    It implements an OpenAPI subset: no `additionalProperties`, no `$defs`/`$ref`
    (refs must be inlined), no `maxLength`. Sending them is a 400.
    """
    defs = schema.get("$defs", {})

    def clean(node):
        if isinstance(node, list):
            return [clean(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(defs.get(node["$ref"].split("/")[-1], {}))
        out = {}
        for k, v in node.items():
            if k in ("additionalProperties", "$defs", "maxLength", "title", "default"):
                continue
            # Gemini has no anyOf; collapse `X | null` to the non-null branch
            # and let `nullable` carry the rest.
            if k == "anyOf":
                branches = [b for b in v if b.get("type") != "null"]
                picked = clean(branches[0]) if branches else {"type": "string"}
                picked["nullable"] = len(branches) < len(v)
                return picked
            out[k] = clean(v)
        return out

    return clean({k: v for k, v in schema.items() if k != "$defs"})
