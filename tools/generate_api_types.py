"""Generate TypeScript interfaces from the API's Pydantic models.

Development tooling. Never shipped, and never run at request time — the
output is committed and a test asserts it still matches, the same shape
as the warehouse schema-drift guard.

**It raises on any construct it does not understand.** That is the whole
reason this exists rather than `openapi-typescript`: a general tool emits
`any` for anything it cannot express, and `any` is a type that checks
nothing while looking like it does. A field this generator cannot type
must fail the build, not degrade into one that silently stops checking —
the same stance the stub takes on an unregistered prompt.

The cost is coverage: it handles the constructs our models actually use
and nothing else. If that becomes a burden as the models grow, switching
to `openapi-typescript` is the revisit trigger — the argument for this
generator is the raise, not the sixty lines.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.api import schemas

HEADER = """\
// GENERATED FILE — do not edit.
//
// Produced from the Pydantic response models in app/api/schemas.py by
// tools/generate_api_types.py. A test regenerates this and fails if it
// differs, so editing it by hand will not survive the gate.
//
// Regenerate with:  python -m tools.generate_api_types
"""

#: Models to emit, in dependency order so a referenced interface is
#: declared before the one referencing it.
MODELS: tuple[type[BaseModel], ...] = (
    schemas.Bin,
    schemas.CohortOverview,
    schemas.EngagementPoint,
    schemas.EngagementTrend,
    schemas.RankedLearner,
    schemas.LearnerRanking,
    schemas.Driver,
    schemas.LearnerDetail,
    schemas.LearnerSummary,
    schemas.Question,
    schemas.Answer,
)

DEFAULT_PATH = Path("app/web/src/api-types.ts")

#: The ONLY fields allowed an unconstrained value, named as
#: `Model.field` so the exemption cannot spread by accident.
#:
#: A cited row's columns genuinely vary between runs. That is not a
#: modelling failure: **docs/adr/0008-grounding-strategy.md** records it
#: as a measured finding — nine of twelve golden questions produced a
#: different SQL statement across five runs, four never repeating one,
#: while their answers stayed identical. No fixed interface describes a
#: row whose columns are chosen per run. They emit `unknown`, never `any`: `unknown`
#: forces the consumer to narrow before using the value, `any` lets them
#: skip that and calls it typed. Keep this list short; every entry is a
#: place the compiler stops helping.
DYNAMIC_FIELDS: frozenset[str] = frozenset({"Answer.citations"})

_PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
}


class UnsupportedSchema(TypeError):
    """A construct this generator cannot express as a checked type.

    Raised rather than emitting ``any``. Whoever meets this either adds
    the construct here or reconsiders the model — both better than a
    field that type-checks against nothing.
    """


def _type_of(schema: dict, name: str) -> str:
    """Translate one JSON-schema node into a TypeScript type.

    Args:
        schema: The JSON-schema node.
        name: ``Model.field``, used in errors and to check the dynamic
            exemption.
    """
    if name in DYNAMIC_FIELDS and schema.get("type") == "object":
        return "Record<string, unknown>"

    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]

    # Optional fields arrive as anyOf[T, null].
    if "anyOf" in schema:
        options = [item for item in schema["anyOf"] if item.get("type") != "null"]
        nullable = len(options) != len(schema["anyOf"])
        if len(options) != 1:
            raise UnsupportedSchema(
                f"{name}: a union of {len(options)} non-null members has no "
                "obvious TypeScript form. Emitting `any` would type-check "
                "against nothing; name the union explicitly instead."
            )
        inner = _type_of(options[0], name)
        return f"{inner} | null" if nullable else inner

    kind = schema.get("type")

    if kind == "string":
        # date and date-time both serialise as ISO strings over the wire.
        return "string"
    if kind in _PRIMITIVES:
        return _PRIMITIVES[kind]
    if kind == "array":
        items = schema.get("items")
        if not items:
            raise UnsupportedSchema(
                f"{name}: an array with no declared item type. `any[]` checks nothing."
            )
        return f"{_type_of(items, name)}[]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if extra in (True, None):
            # Not exempt, so this is a modelling gap rather than genuine
            # dynamism. Adding it to DYNAMIC_FIELDS is a deliberate act.
            raise UnsupportedSchema(
                f"{name}: an object with unconstrained properties. Declare "
                "the value type, or model it explicitly."
            )
        if extra is False:
            raise UnsupportedSchema(
                f"{name}: an object with no properties and none allowed."
            )
        return f"Record<string, {_type_of(extra, name)}>"

    raise UnsupportedSchema(
        f"{name}: unhandled schema {schema!r}. This generator raises rather "
        "than emitting `any`, because a type that checks nothing while "
        "looking like it does is worse than a failed build. Add the "
        "construct here, or use a shape the generator understands."
    )


def render(model: type[BaseModel]) -> str:
    """One TypeScript interface for one model."""
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    required = set(schema.get("required", []))

    lines = [f"export interface {model.__name__} {{"]
    for field, node in schema.get("properties", {}).items():
        optional = "" if field in required else "?"
        qualified = f"{model.__name__}.{field}"
        lines.append(f"  {field}{optional}: {_type_of(node, qualified)};")
    lines.append("}")
    return "\n".join(lines)


def generate(models: tuple[type[BaseModel], ...] = MODELS) -> str:
    """The whole file."""
    return HEADER + "\n" + "\n\n".join(render(model) for model in models) + "\n"


def main() -> int:
    """Write the file. Prints where it went."""
    text = generate()
    DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {DEFAULT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
