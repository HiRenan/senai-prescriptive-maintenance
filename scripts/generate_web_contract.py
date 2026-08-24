"""Render or verify the web analysis contract derived from OpenAPI v1."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, cast

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
OPENAPI_SNAPSHOT: Final = REPOSITORY_ROOT / "apps" / "api" / "openapi" / "v1.json"
GENERATED_ROOT: Final = REPOSITORY_ROOT / "apps" / "web" / "src" / "generated"
RUNTIME_MODULE: Final = GENERATED_ROOT / "analysis-contract.js"
TYPES_MODULE: Final = GENERATED_ROOT / "analysis-contract.d.ts"

ANALYSIS_PATH: Final = "/analysis"
SCHEMA_PREFIX: Final = "#/components/schemas/"
BANNER: Final = (
    "// Generated from apps/api/openapi/v1.json by "
    "scripts/generate_web_contract.py.\n"
    "// Do not edit by hand; run the generator and commit the result.\n"
)


class ContractGenerationError(RuntimeError):
    """Describe a snapshot the generator refuses to interpret."""


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractGenerationError(f"{context} deveria ser um objeto.")
    mapping = cast(Mapping[object, object], value)
    resolved: dict[str, object] = {}
    for key, nested in mapping.items():
        if not isinstance(key, str):
            raise ContractGenerationError(f"{context} tem chave não textual.")
        resolved[key] = nested
    return resolved


def _sequence(value: object, context: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ContractGenerationError(f"{context} deveria ser uma lista.")
    return cast(Sequence[object], value)


def _text(value: object, context: str) -> str:
    if not isinstance(value, str):
        raise ContractGenerationError(f"{context} deveria ser texto.")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractGenerationError(f"{context} deveria ser numérico.")
    return float(value)


def _integer_like(value: object, context: str) -> int:
    # JSON Schema publishes integral bounds as JSON numbers such as ``10.0``.
    number = _number(value, context)
    if number != int(number):
        raise ContractGenerationError(f"{context} deveria ser integral.")
    return int(number)


def _entry(mapping: Mapping[str, object], key: str, context: str) -> object:
    if key not in mapping:
        raise ContractGenerationError(f"{context} não declara '{key}'.")
    return mapping[key]


def _schema_name(reference: str) -> str:
    if not reference.startswith(SCHEMA_PREFIX):
        raise ContractGenerationError(f"Referência fora de components: {reference}.")
    return reference[len(SCHEMA_PREFIX) :]


class Contract:
    """Read-only accessor over the schemas reachable from ``POST /analysis``."""

    def __init__(self, document: Mapping[str, object]) -> None:
        components = _mapping(
            _entry(document, "components", "O documento"),
            "components",
        )
        self._schemas = _mapping(
            _entry(components, "schemas", "components"),
            "components.schemas",
        )
        info = _mapping(_entry(document, "info", "O documento"), "info")
        self.version = _text(_entry(info, "version", "info"), "info.version")
        paths = _mapping(_entry(document, "paths", "O documento"), "paths")
        self.operation = _mapping(
            _entry(
                _mapping(_entry(paths, ANALYSIS_PATH, "paths"), ANALYSIS_PATH),
                "post",
                ANALYSIS_PATH,
            ),
            f"{ANALYSIS_PATH}.post",
        )

    def schema(self, name: str) -> Mapping[str, object]:
        return _mapping(
            _entry(self._schemas, name, "components.schemas"),
            f"components.schemas.{name}",
        )

    def resolve(self, node: Mapping[str, object], context: str) -> str:
        reference = _entry(node, "$ref", context)
        return _schema_name(_text(reference, f"{context}.$ref"))

    def property_schema(
        self,
        schema_name: str,
        property_name: str,
    ) -> Mapping[str, object]:
        properties = _mapping(
            _entry(self.schema(schema_name), "properties", schema_name),
            f"{schema_name}.properties",
        )
        return _mapping(
            _entry(properties, property_name, f"{schema_name}.properties"),
            f"{schema_name}.properties.{property_name}",
        )


def _request_schema_name(contract: Contract) -> str:
    body = _mapping(
        _entry(contract.operation, "requestBody", f"{ANALYSIS_PATH}.post"),
        "requestBody",
    )
    content = _mapping(_entry(body, "content", "requestBody"), "requestBody.content")
    media = _mapping(
        _entry(content, "application/json", "requestBody.content"),
        "requestBody.content.application/json",
    )
    return contract.resolve(
        _mapping(_entry(media, "schema", "requestBody media"), "requestBody schema"),
        "requestBody schema",
    )


def _request_examples(contract: Contract) -> tuple[tuple[str, str, object], ...]:
    body = _mapping(
        _entry(contract.operation, "requestBody", f"{ANALYSIS_PATH}.post"),
        "requestBody",
    )
    content = _mapping(_entry(body, "content", "requestBody"), "requestBody.content")
    media = _mapping(
        _entry(content, "application/json", "requestBody.content"),
        "requestBody.content.application/json",
    )
    examples = _mapping(
        _entry(media, "examples", "requestBody media"),
        "requestBody examples",
    )
    collected: list[tuple[str, str, object]] = []
    for name, raw in examples.items():
        example = _mapping(raw, f"exemplo {name}")
        summary = _text(_entry(example, "summary", name), f"{name}.summary")
        collected.append((name, summary, _entry(example, "value", name)))
    return tuple(collected)


def _feature_fields(contract: Contract) -> tuple[Mapping[str, object], ...]:
    request = _request_schema_name(contract)
    features_name = contract.resolve(
        contract.property_schema(request, "features"),
        f"{request}.features",
    )
    features = contract.schema(features_name)
    properties = _mapping(
        _entry(features, "properties", features_name),
        f"{features_name}.properties",
    )
    required = tuple(
        _text(name, f"{features_name}.required")
        for name in _sequence(
            _entry(features, "required", features_name),
            f"{features_name}.required",
        )
    )
    fields: list[Mapping[str, object]] = []
    for name in properties:
        if name not in required:
            raise ContractGenerationError(f"A feature '{name}' não é obrigatória.")
        field = _mapping(properties[name], f"{features_name}.{name}")
        minimum = field.get("minimum")
        maximum = field.get("maximum")
        fields.append(
            {
                "name": name,
                "title": _text(_entry(field, "title", name), f"{name}.title"),
                "minimum": (
                    None if minimum is None else _number(minimum, f"{name}.minimum")
                ),
                "maximum": (
                    None if maximum is None else _number(maximum, f"{name}.maximum")
                ),
            }
        )
    if len(fields) != len(required):
        raise ContractGenerationError("As features declaradas divergem das exigidas.")
    return tuple(fields)


def _top_k_bounds(contract: Contract) -> Mapping[str, int]:
    request = _request_schema_name(contract)
    top_k = contract.property_schema(request, "top_k")
    return {
        "default": _integer_like(
            _entry(top_k, "default", "top_k"),
            "top_k.default",
        ),
        "minimum": _integer_like(
            _entry(top_k, "minimum", "top_k"),
            "top_k.minimum",
        ),
        "maximum": _integer_like(
            _entry(top_k, "maximum", "top_k"),
            "top_k.maximum",
        ),
    }


def _published_statuses(contract: Contract) -> tuple[int, ...]:
    """Read every HTTP status the operation publishes, in ascending order."""
    responses = _mapping(
        _entry(contract.operation, "responses", f"{ANALYSIS_PATH}.post"),
        "responses",
    )
    statuses = sorted(int(key) for key in responses if key.isdigit())
    if not statuses:
        raise ContractGenerationError(
            f"{ANALYSIS_PATH}.post deveria publicar ao menos um status."
        )
    return tuple(statuses)


def _success_status(contract: Contract) -> int:
    """Read the single successful status the operation publishes."""
    statuses = [
        status for status in _published_statuses(contract) if 200 <= status < 300
    ]
    if len(statuses) != 1:
        raise ContractGenerationError(
            f"{ANALYSIS_PATH}.post deveria publicar exatamente um status de sucesso."
        )
    return statuses[0]


def _response_schema_name(contract: Contract) -> str:
    responses = _mapping(
        _entry(contract.operation, "responses", f"{ANALYSIS_PATH}.post"),
        "responses",
    )
    success = _mapping(_entry(responses, "200", "responses"), "responses.200")
    content = _mapping(_entry(success, "content", "responses.200"), "200.content")
    media = _mapping(
        _entry(content, "application/json", "200.content"),
        "200.content.application/json",
    )
    return contract.resolve(
        _mapping(_entry(media, "schema", "200 media"), "200 schema"),
        "200 schema",
    )


def _const_of(contract: Contract, schema_name: str, property_name: str) -> str:
    field = contract.property_schema(schema_name, property_name)
    return _text(
        _entry(field, "const", f"{schema_name}.{property_name}"),
        f"{schema_name}.{property_name}.const",
    )


def _is_null_schema(node: Mapping[str, object]) -> bool:
    return node.get("type") == "null"


def _outcome_rows(contract: Contract) -> tuple[Mapping[str, object], ...]:
    union = contract.schema(_response_schema_name(contract))
    variants = _sequence(
        _entry(union, "oneOf", "AnalysisResponse"),
        "AnalysisResponse.oneOf",
    )
    rows: list[Mapping[str, object]] = []
    for variant in variants:
        name = contract.resolve(_mapping(variant, "oneOf item"), "oneOf item")
        diagnosis = contract.property_schema(name, "diagnosis")
        abstention = contract.property_schema(name, "abstention")
        prescription = contract.property_schema(name, "prescription")
        support = contract.property_schema(name, "support")
        citations = contract.property_schema(name, "citations")
        rows.append(
            {
                "outcome": _const_of(contract, name, "outcome"),
                "schema": name,
                "hasDiagnosis": not _is_null_schema(diagnosis),
                "hasAbstention": not _is_null_schema(abstention),
                "prescribes": not _is_null_schema(prescription),
                "abstentionReason": (
                    None
                    if _is_null_schema(abstention)
                    else _const_of(
                        contract,
                        contract.resolve(abstention, f"{name}.abstention"),
                        "reason",
                    )
                ),
                "supportLevel": _const_of(
                    contract,
                    contract.resolve(support, f"{name}.support"),
                    "level",
                ),
                "maxCitations": _integer_like(
                    _entry(citations, "maxItems", f"{name}.citations"),
                    f"{name}.citations.maxItems",
                ),
            }
        )
    return tuple(rows)


def _priorities(contract: Contract) -> tuple[str, ...]:
    schema = contract.schema("PrescriptionPriority")
    values = _sequence(
        _entry(schema, "enum", "PrescriptionPriority"),
        "PrescriptionPriority.enum",
    )
    return tuple(_text(value, "PrescriptionPriority.enum") for value in values)


def _literal(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return json.dumps(int(value), ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=False)


def _optional_number(
    node: Mapping[str, object],
    key: str,
    context: str,
) -> float | None:
    value = node.get(key)
    return None if value is None else _number(value, f"{context}.{key}")


def _optional_integer(
    node: Mapping[str, object],
    key: str,
    context: str,
) -> int | None:
    value = node.get(key)
    return None if value is None else _integer_like(value, f"{context}.{key}")


# A pattern crosses from Python to JavaScript unchanged only inside a narrow
# subset. Anything outside it is refused here instead of being reinterpreted by
# a second engine with different rules.
PATTERN_CHARACTERS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_-.:/ ^$[](){}|?*+,\\"
)
# Only escapes that turn a metacharacter into itself. The shorthand classes are
# excluded on purpose: Python reads `\d`, `\w` and `\s` as Unicode-aware by
# default, while `RegExp` reads them as ASCII, so the same text would mean two
# different rules on the two sides of the contract.
PATTERN_ESCAPES: Final = frozenset(".-+*?()[]{}|^$/\\")
UNSUPPORTED_PATTERN_CONSTRUCTS: Final = (
    "(?<",
    "(?P",
    "(?#",
    "(?i",
    "(?m",
    "(?s",
    "(?x",
    "(?a",
    "(?u",
    "[[:",
)


def _string_pattern(node: Mapping[str, object], context: str) -> str | None:
    """Read a string pattern the browser can apply with the same meaning."""
    raw = node.get("pattern")
    if raw is None:
        return None
    pattern = _text(raw, f"{context}.pattern")
    if not pattern.startswith("^") or not pattern.endswith("$"):
        raise ContractGenerationError(
            f"{context}.pattern precisa estar ancorado em '^' e '$'."
        )
    for construct in UNSUPPORTED_PATTERN_CONSTRUCTS:
        if construct in pattern:
            raise ContractGenerationError(
                f"{context}.pattern usa construção não suportada: {construct}."
            )
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            escaped = pattern[index + 1 : index + 2]
            if escaped not in PATTERN_ESCAPES:
                raise ContractGenerationError(
                    f"{context}.pattern usa escape não suportado: '\\{escaped}'."
                )
            index += 2
            continue
        if character not in PATTERN_CHARACTERS:
            raise ContractGenerationError(
                f"{context}.pattern usa caractere não suportado: {character!r}."
            )
        index += 1
    try:
        re.compile(pattern)
    except re.error as error:
        raise ContractGenerationError(
            f"{context}.pattern não é uma expressão válida: {error}."
        ) from None
    return pattern


def _schema_node(
    contract: Contract,
    node: Mapping[str, object],
    context: str,
    pending: list[str],
) -> Mapping[str, object]:
    """Translate one property schema into a node the decoder can walk."""
    if "$ref" in node:
        name = contract.resolve(node, context)
        target = contract.schema(name)
        if "enum" in target:
            values = _sequence(target["enum"], f"{name}.enum")
            return {
                "kind": "enum",
                "values": [_text(value, f"{name}.enum") for value in values],
            }
        pending.append(name)
        return {"kind": "object", "schema": name}
    declared = _text(_entry(node, "type", context), f"{context}.type")
    if declared == "null":
        return {"kind": "null"}
    if declared == "string":
        if "const" in node:
            return {"kind": "const", "value": _text(node["const"], f"{context}.const")}
        return {
            "kind": "string",
            "minLength": _optional_integer(node, "minLength", context),
            "maxLength": _optional_integer(node, "maxLength", context),
            "pattern": _string_pattern(node, context),
        }
    if declared in {"integer", "number"}:
        return {
            "kind": declared,
            "minimum": _optional_number(node, "minimum", context),
            "maximum": _optional_number(node, "maximum", context),
        }
    if declared == "array":
        items = _mapping(_entry(node, "items", context), f"{context}.items")
        return {
            "kind": "array",
            "items": _schema_node(contract, items, f"{context}.items", pending),
            "minItems": _optional_integer(node, "minItems", context),
            "maxItems": _optional_integer(node, "maxItems", context),
        }
    raise ContractGenerationError(f"{context} usa um tipo não suportado: {declared}.")


def _response_schemas(
    contract: Contract,
) -> tuple[tuple[str, Mapping[str, object]], ...]:
    """Collect every object schema a `200` body can contain, in closed form."""
    pending = [str(row["schema"]) for row in _outcome_rows(contract)]
    seen: dict[str, Mapping[str, object]] = {}
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        schema = contract.schema(name)
        if schema.get("additionalProperties") is not False:
            raise ContractGenerationError(
                f"{name} precisa recusar propriedades extras no contrato."
            )
        properties = _mapping(
            _entry(schema, "properties", name),
            f"{name}.properties",
        )
        required = [
            _text(entry, f"{name}.required")
            for entry in _sequence(_entry(schema, "required", name), f"{name}.required")
        ]
        if sorted(required) != sorted(properties):
            raise ContractGenerationError(
                f"{name} precisa exigir exatamente as propriedades declaradas."
            )
        seen[name] = {
            "required": required,
            "properties": {
                key: _schema_node(
                    contract,
                    _mapping(value, f"{name}.properties.{key}"),
                    f"{name}.properties.{key}",
                    pending,
                )
                for key, value in properties.items()
            },
        }
    return tuple(seen.items())


def _render_node(node: Mapping[str, object], indent: str) -> str:
    entries = [
        f"{key}: {_literal(value)}" for key, value in node.items() if key != "items"
    ]
    if "items" in node:
        items = _mapping(node["items"], "items")
        entries.append(f"items: {_render_node(items, indent + '  ')}")
    joined = ", ".join(entries)
    inline = f"Object.freeze({{ {joined} }})"
    if len(indent) + len(inline) <= 78:
        return inline
    body = "".join(f"{indent}  {entry},\n" for entry in entries)
    return f"Object.freeze({{\n{body}{indent}}})"


def _field_description(contract: Contract, schema: str, field: str) -> str:
    node = contract.property_schema(schema, field)
    return _text(
        _entry(node, "description", f"{schema}.{field}"),
        f"{schema}.{field}.description",
    )


def _block_literal(value: object, indent: str) -> str:
    dumped = json.dumps(value, ensure_ascii=False, indent=2)
    return f"\n{indent}".join(dumped.splitlines())


def _render_runtime(contract: Contract) -> str:
    fields = _feature_fields(contract)
    outcomes = _outcome_rows(contract)
    top_k = _top_k_bounds(contract)
    lines = [
        BANNER.rstrip(),
        "",
        f"export const API_CONTRACT_VERSION = {_literal(contract.version)};",
        "",
    ]
    lines.append(f"export const ANALYSIS_PATH = {_literal(ANALYSIS_PATH)};")
    lines.append("")
    status = _success_status(contract)
    lines.append(f"export const ANALYSIS_SUCCESS_STATUS = {_literal(status)};")
    lines.append("")
    published = ", ".join(_literal(entry) for entry in _published_statuses(contract))
    lines.append(f"export const ANALYSIS_STATUSES = Object.freeze([{published}]);")
    lines.append("")
    support_note = _field_description(contract, "SufficientSupport", "support_score")
    lines.append(f"export const SUPPORT_SCORE_NOTE = {_literal(support_note)};")
    lines.append("")
    distance_note = _field_description(contract, "OpaqueNeighbor", "distance")
    lines.append(f"export const NEIGHBOR_DISTANCE_NOTE = {_literal(distance_note)};")
    lines.append("")
    lines.append("export const RESPONSE_SCHEMAS = Object.freeze({")
    for name, schema in _response_schemas(contract):
        required = _sequence(schema["required"], f"{name}.required")
        properties = _mapping(schema["properties"], f"{name}.properties")
        lines.append(f"  {name}: Object.freeze({{")
        lines.append("    required: Object.freeze([")
        for entry in required:
            lines.append(f"      {_literal(entry)},")
        lines.append("    ]),")
        lines.append("    properties: Object.freeze({")
        for key, node in properties.items():
            rendered = _render_node(
                _mapping(node, f"{name}.properties.{key}"),
                "      ",
            )
            lines.append(f"      {key}: {rendered},")
        lines.append("    }),")
        lines.append("  }),")
    lines.append("});")
    lines.append("")
    lines.append("export const FEATURE_FIELDS = Object.freeze([")
    for field in fields:
        lines.append("  Object.freeze({")
        lines.append(f"    name: {_literal(field['name'])},")
        lines.append(f"    title: {_literal(field['title'])},")
        lines.append(f"    minimum: {_literal(field['minimum'])},")
        lines.append(f"    maximum: {_literal(field['maximum'])},")
        lines.append("  }),")
    lines.append("]);")
    lines.append("")
    lines.append("export const TOP_K = Object.freeze({")
    lines.append(f"  fallback: {_literal(top_k['default'])},")
    lines.append(f"  minimum: {_literal(top_k['minimum'])},")
    lines.append(f"  maximum: {_literal(top_k['maximum'])},")
    lines.append("});")
    lines.append("")
    lines.append("export const ANALYSIS_OUTCOMES = Object.freeze([")
    for row in outcomes:
        lines.append("  Object.freeze({")
        lines.append(f"    outcome: {_literal(row['outcome'])},")
        lines.append(f"    schema: {_literal(row['schema'])},")
        lines.append(f"    hasDiagnosis: {_literal(row['hasDiagnosis'])},")
        lines.append(f"    hasAbstention: {_literal(row['hasAbstention'])},")
        lines.append(f"    abstentionReason: {_literal(row['abstentionReason'])},")
        lines.append(f"    supportLevel: {_literal(row['supportLevel'])},")
        lines.append(f"    prescribes: {_literal(row['prescribes'])},")
        lines.append(f"    maxCitations: {_literal(row['maxCitations'])},")
        lines.append("  }),")
    lines.append("]);")
    lines.append("")
    lines.append("export const PRESCRIPTION_PRIORITIES = Object.freeze([")
    for priority in _priorities(contract):
        lines.append(f"  {_literal(priority)},")
    lines.append("]);")
    lines.append("")
    lines.append("export const SYNTHETIC_ANALYSIS_EXAMPLES = Object.freeze([")
    for name, summary, value in _request_examples(contract):
        lines.append("  Object.freeze({")
        lines.append(f"    name: {_literal(name)},")
        lines.append(f"    summary: {_literal(summary)},")
        lines.append(f"    request: {_block_literal(value, '    ')},")
        lines.append("  }),")
    lines.append("]);")
    lines.append("")
    return "\n".join(lines)


def _type_expression(node: Mapping[str, object], context: str) -> str:
    if "$ref" in node:
        return _schema_name(_text(node["$ref"], f"{context}.$ref"))
    if "oneOf" in node:
        variants = _sequence(node["oneOf"], f"{context}.oneOf")
        return " | ".join(
            _type_expression(_mapping(variant, context), context)
            for variant in variants
        )
    if "const" in node:
        return _literal(node["const"])
    if "enum" in node:
        values = _sequence(node["enum"], f"{context}.enum")
        return " | ".join(_literal(value) for value in values)
    declared = node.get("type")
    if declared == "null":
        return "null"
    if declared in {"integer", "number"}:
        return "number"
    if declared == "boolean":
        return "boolean"
    if declared == "string":
        return "string"
    if declared == "array":
        items = _mapping(_entry(node, "items", context), f"{context}.items")
        return f"readonly {_type_expression(items, f'{context}.items')}[]"
    if declared == "object":
        return "Readonly<Record<string, unknown>>"
    raise ContractGenerationError(f"{context} usa um tipo não suportado.")


def _collect_schema_names(
    contract: Contract,
    roots: Sequence[str],
) -> tuple[str, ...]:
    ordered: list[str] = []
    pending = list(roots)
    while pending:
        name = pending.pop(0)
        if name in ordered:
            continue
        ordered.append(name)
        for reference in _iter_references(contract.schema(name)):
            if reference not in ordered:
                pending.append(reference)
    return tuple(ordered)


def _iter_references(value: object) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, nested in mapping.items():
            if key == "$ref" and isinstance(nested, str):
                found.append(_schema_name(nested))
            else:
                found.extend(_iter_references(nested))
    elif not isinstance(value, (str, bytes)) and isinstance(value, Sequence):
        for nested in cast(Sequence[object], value):
            found.extend(_iter_references(nested))
    return tuple(found)


def _render_declaration(contract: Contract, name: str) -> str:
    schema = contract.schema(name)
    if "oneOf" in schema:
        union = "\n".join(
            f"  | {_type_expression(_mapping(variant, name), name)}"
            for variant in _sequence(schema["oneOf"], f"{name}.oneOf")
        )
        return f"export type {name} =\n{union};\n"
    if "enum" in schema or "const" in schema:
        return f"export type {name} = {_type_expression(schema, name)};\n"
    properties = _mapping(_entry(schema, "properties", name), f"{name}.properties")
    required = frozenset(
        _text(entry, f"{name}.required")
        for entry in _sequence(schema.get("required", []), f"{name}.required")
    )
    lines = [f"export interface {name} {{"]
    for property_name in properties:
        field = _mapping(properties[property_name], f"{name}.{property_name}")
        optional = "" if property_name in required else "?"
        expression = _type_expression(field, f"{name}.{property_name}")
        lines.append(f"  readonly {property_name}{optional}: {expression};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_types(contract: Contract) -> str:
    roots = (
        _request_schema_name(contract),
        _response_schema_name(contract),
        "ErrorResponse",
    )
    names = _collect_schema_names(contract, roots)
    blocks = [BANNER]
    for name in names:
        blocks.append(_render_declaration(contract, name))
    response = _response_schema_name(contract)
    blocks.append(f'export type AnalysisOutcome = {response}["outcome"];\n')
    blocks.append(
        "export interface FeatureField {\n"
        "  readonly name: keyof AnalysisFeatures;\n"
        "  readonly title: string;\n"
        "  readonly minimum: number | null;\n"
        "  readonly maximum: number | null;\n"
        "}\n"
    )
    blocks.append(
        "export interface OutcomeContract {\n"
        "  readonly outcome: AnalysisOutcome;\n"
        "  readonly schema: string;\n"
        "  readonly hasDiagnosis: boolean;\n"
        "  readonly hasAbstention: boolean;\n"
        "  readonly abstentionReason: string | null;\n"
        "  readonly supportLevel: string;\n"
        "  readonly prescribes: boolean;\n"
        "  readonly maxCitations: number;\n"
        "}\n"
    )
    blocks.append(
        "export interface SyntheticAnalysisExample {\n"
        "  readonly name: string;\n"
        "  readonly summary: string;\n"
        "  readonly request: AnalysisRequest;\n"
        "}\n"
    )
    blocks.append("export declare const API_CONTRACT_VERSION: string;\n")
    blocks.append("export declare const ANALYSIS_PATH: string;\n")
    blocks.append("export declare const ANALYSIS_SUCCESS_STATUS: number;\n")
    blocks.append("export declare const ANALYSIS_STATUSES: readonly number[];\n")
    blocks.append("export declare const SUPPORT_SCORE_NOTE: string;\n")
    blocks.append("export declare const NEIGHBOR_DISTANCE_NOTE: string;\n")
    blocks.append(
        "export type SchemaNode =\n"
        '  | { readonly kind: "null" }\n'
        '  | { readonly kind: "const"; readonly value: string }\n'
        "  | {\n"
        '      readonly kind: "string";\n'
        "      readonly minLength: number | null;\n"
        "      readonly maxLength: number | null;\n"
        "      readonly pattern: string | null;\n"
        "    }\n"
        "  | {\n"
        '      readonly kind: "integer" | "number";\n'
        "      readonly minimum: number | null;\n"
        "      readonly maximum: number | null;\n"
        "    }\n"
        "  | {\n"
        '      readonly kind: "array";\n'
        "      readonly items: SchemaNode;\n"
        "      readonly minItems: number | null;\n"
        "      readonly maxItems: number | null;\n"
        "    }\n"
        '  | { readonly kind: "enum"; readonly values: readonly string[] }\n'
        '  | { readonly kind: "object"; readonly schema: string };\n'
    )
    blocks.append(
        "export interface ObjectSchema {\n"
        "  readonly required: readonly string[];\n"
        "  readonly properties: Readonly<Record<string, SchemaNode>>;\n"
        "}\n"
    )
    blocks.append(
        "export declare const RESPONSE_SCHEMAS: Readonly<\n"
        "  Record<string, ObjectSchema>\n"
        ">;\n"
    )
    blocks.append("export declare const FEATURE_FIELDS: readonly FeatureField[];\n")
    blocks.append(
        "export declare const TOP_K: {\n"
        "  readonly fallback: number;\n"
        "  readonly minimum: number;\n"
        "  readonly maximum: number;\n"
        "};\n"
    )
    blocks.append(
        "export declare const ANALYSIS_OUTCOMES: readonly OutcomeContract[];\n"
    )
    blocks.append(
        "export declare const PRESCRIPTION_PRIORITIES: readonly "
        "PrescriptionPriority[];\n"
    )
    blocks.append(
        "export declare const SYNTHETIC_ANALYSIS_EXAMPLES: readonly "
        "SyntheticAnalysisExample[];\n"
    )
    return "\n".join(blocks)


def _rendered_modules() -> tuple[tuple[Path, bytes], ...]:
    document = _mapping(
        json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8")),
        "O snapshot",
    )
    contract = Contract(document)
    return (
        (RUNTIME_MODULE, _render_runtime(contract).encode("utf-8")),
        (TYPES_MODULE, _render_types(contract).encode("utf-8")),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Falha se o contrato web rastreado divergir do OpenAPI v1.",
    )
    arguments = parser.parse_args()
    try:
        modules = _rendered_modules()
    except (ContractGenerationError, OSError) as error:
        raise SystemExit(f"Contrato web não pôde ser derivado: {error}") from None

    if arguments.check:
        for path, expected in modules:
            try:
                actual = path.read_bytes()
            except OSError:
                raise SystemExit(f"O módulo {path.name} está ausente.") from None
            if actual != expected:
                raise SystemExit(f"O módulo {path.name} está desatualizado.")
        return

    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    for path, expected in modules:
        path.write_bytes(expected)


if __name__ == "__main__":
    main()
