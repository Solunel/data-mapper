"""把当前 Definition / Knowledge JSON 加载为只读 OntologyCatalog。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import unicodedata

from .metric_resolution_contracts import OntologyCatalog, OntologyMetric
from .observation_contracts import ObservationSchema, SchemaField


class OntologyCatalogError(ValueError):
    """当前本体资产无法提供 Phase 2 所需目录时抛出。"""


_ACTUAL_REQUIRED = {
    "id",
    "organization_id",
    "metric_id",
    "business_scope",
    "source",
    "period",
    "actual_value",
    "unit",
    "status",
}
_PERIOD_REQUIRED = {"period_type", "period_key", "period_basis"}
_PERIOD_BASIS_REQUIRED = {
    "PERIOD_VALUE",
    "YEAR_TO_DATE",
    "PERIOD_BEGIN",
    "PERIOD_END",
}


def load_ontology_catalog(
    definition_path: str | Path,
    knowledge_path: str | Path,
) -> OntologyCatalog:
    """只读加载 JSON；路径与源结构不会泄漏到 Mapping Core。"""

    definition = _read_json(definition_path, "Definition")
    knowledge = _read_json(knowledge_path, "Knowledge")
    return build_ontology_catalog(definition, knowledge)


def build_ontology_catalog(
    definition: Mapping[str, Any],
    knowledge: Mapping[str, Any],
) -> OntologyCatalog:
    object_types = _mapping(definition.get("object_types"), "Definition.object_types")
    structs = _mapping(definition.get("structs"), "Definition.structs")
    enums = _mapping(definition.get("enums"), "Definition.enums")

    actual = _mapping(object_types.get("ActualObservation"), "ActualObservation")
    actual_properties = _mapping(
        actual.get("properties"), "ActualObservation.properties"
    )
    _expect_spec(actual_properties, "id", "string")
    _expect_spec(actual_properties, "organization_id", "reference", "reference", "Organization.id")
    _expect_spec(actual_properties, "metric_id", "reference", "reference", "Metric.id")
    _expect_spec(actual_properties, "business_scope", "string")
    _expect_spec(actual_properties, "source", "string")
    _expect_spec(actual_properties, "period", "struct", "struct", "Period")
    _expect_spec(actual_properties, "actual_value", "number")
    _expect_spec(actual_properties, "unit", "enum", "enum", "Unit")
    _expect_spec(actual_properties, "status", "enum", "enum", "Status")
    actual_required = tuple(
        name
        for name, spec in actual_properties.items()
        if _mapping(spec, f"ActualObservation.{name}").get("required") is True
    )
    missing_actual = sorted(_ACTUAL_REQUIRED.difference(actual_required))
    if missing_actual:
        raise OntologyCatalogError(
            "ActualObservation 缺少 Phase 2 必需约束：" + ", ".join(missing_actual)
        )

    period = _mapping(structs.get("Period"), "Definition.structs.Period")
    period_fields = _mapping(period.get("fields"), "Period.fields")
    _expect_spec(period_fields, "period_type", "enum", "enum", "PeriodType")
    _expect_spec(period_fields, "period_key", "string")
    _expect_spec(period_fields, "period_basis", "enum", "enum", "PeriodBasis")
    period_required = tuple(
        name
        for name, spec in period_fields.items()
        if _mapping(spec, f"Period.{name}").get("required") is True
    )
    missing_period = sorted(_PERIOD_REQUIRED.difference(period_required))
    if missing_period:
        raise OntologyCatalogError(
            "Period 缺少 Phase 2 必需字段：" + ", ".join(missing_period)
        )

    period_basis_values = _enum_values(enums, "PeriodBasis")
    missing_basis = sorted(_PERIOD_BASIS_REQUIRED.difference(period_basis_values))
    if missing_basis:
        raise OntologyCatalogError(
            "PeriodBasis 缺少 Phase 2 必需值：" + ", ".join(missing_basis)
        )
    period_type_values = _enum_values(enums, "PeriodType")
    status_values = _enum_values(enums, "Status")
    unit_specs = _mapping(
        _mapping(enums.get("Unit"), "Definition.enums.Unit").get("values"),
        "Definition.enums.Unit.values",
    )
    unit_values = {
        key: str(_mapping(value, f"Unit.{key}").get("display_name_cn", key))
        for key, value in unit_specs.items()
    }

    raw_metrics = knowledge.get("Metric")
    if not isinstance(raw_metrics, list):
        raise OntologyCatalogError("Knowledge.Metric 必须是数组")
    metrics = tuple(_load_metric(item, index) for index, item in enumerate(raw_metrics))
    _validate_metric_conflicts(metrics)

    raw_organizations = knowledge.get("Organization", [])
    if not isinstance(raw_organizations, list):
        raise OntologyCatalogError("Knowledge.Organization 必须是数组")
    organization_ids: list[str] = []
    for index, item in enumerate(raw_organizations):
        entry = _mapping(item, f"Knowledge.Organization[{index}]")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise OntologyCatalogError(f"Knowledge.Organization[{index}].id 无效")
        organization_ids.append(identifier)
    if len(set(organization_ids)) != len(organization_ids):
        raise OntologyCatalogError("Knowledge.Organization 存在重复 id")

    canonical = json.dumps(
        {"definition": definition, "knowledge": knowledge},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    revision = "sha256:" + hashlib.sha256(canonical).hexdigest()
    schema_payload = {
        "actual_observation_fields": [
            _schema_field(name, spec).to_dict()
            for name, spec in actual_properties.items()
        ],
        "period_fields": [
            _schema_field(name, spec).to_dict()
            for name, spec in period_fields.items()
        ],
        "period_constraints": dict(period.get("constraints") or {}),
        "period_basis_values": list(period_basis_values),
        "period_type_values": list(period_type_values),
        "unit_values": unit_values,
        "status_values": list(status_values),
    }
    schema_canonical = json.dumps(
        schema_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    observation_schema = ObservationSchema(
        actual_observation_fields=tuple(
            _schema_field(name, spec) for name, spec in actual_properties.items()
        ),
        period_fields=tuple(
            _schema_field(name, spec) for name, spec in period_fields.items()
        ),
        period_constraints=schema_payload["period_constraints"],
        period_basis_values=period_basis_values,
        period_type_values=period_type_values,
        unit_values=unit_values,
        status_values=status_values,
        fingerprint="sha256:" + hashlib.sha256(schema_canonical).hexdigest(),
    )
    return OntologyCatalog(
        ontology_revision=revision,
        observation_schema=observation_schema,
        organization_ids=tuple(organization_ids),
        metrics=metrics,
    )


def _read_json(path: str | Path, label: str) -> Mapping[str, Any]:
    source = Path(path).resolve(strict=True)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OntologyCatalogError(f"{label} JSON 无法读取或解析：{exc}") from exc
    return _mapping(value, label)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OntologyCatalogError(f"{label} 必须是对象")
    return value


def _enum_values(enums: Mapping[str, Any], name: str) -> tuple[str, ...]:
    enum = _mapping(enums.get(name), f"Definition.enums.{name}")
    values = _mapping(enum.get("values"), f"Definition.enums.{name}.values")
    if not values:
        raise OntologyCatalogError(f"Definition.enums.{name} 不能为空")
    return tuple(values)


def _load_metric(value: Any, index: int) -> OntologyMetric:
    item = _mapping(value, f"Knowledge.Metric[{index}]")
    required = ("id", "name_cn", "status", "version")
    for field in required:
        if not isinstance(item.get(field), str) or not item[field].strip():
            raise OntologyCatalogError(f"Knowledge.Metric[{index}].{field} 无效")
    aliases = item.get("aliases", [])
    labels = item.get("business_labels", [])
    if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
        raise OntologyCatalogError(f"Knowledge.Metric[{index}].aliases 必须是字符串数组")
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise OntologyCatalogError(
            f"Knowledge.Metric[{index}].business_labels 必须是字符串数组"
        )
    semantics = item.get("value_semantics")
    if semantics is not None and not isinstance(semantics, str):
        raise OntologyCatalogError(
            f"Knowledge.Metric[{index}].value_semantics 必须是字符串或空"
        )
    return OntologyMetric(
        current_metric_id=item["id"].strip(),
        name_cn=item["name_cn"].strip(),
        aliases=tuple(alias.strip() for alias in aliases if alias.strip()),
        definition_cn=str(item.get("definition_cn", "")),
        business_labels=tuple(labels),
        value_semantics=semantics,
        status=item["status"].strip(),
        version=item["version"].strip(),
    )


def _validate_metric_conflicts(metrics: tuple[OntologyMetric, ...]) -> None:
    ids: dict[str, str] = {}
    names: dict[str, str] = {}
    for metric in metrics:
        if metric.current_metric_id in ids:
            raise OntologyCatalogError(
                f"Metric id 重复：{metric.current_metric_id}"
            )
        ids[metric.current_metric_id] = metric.name_cn

        name_key = _exact_key(metric.name_cn)
        if name_key in names:
            raise OntologyCatalogError(
                f"Metric 正式名称冲突：{metric.name_cn}"
            )
        names[name_key] = metric.current_metric_id

    aliases: dict[str, str] = {}
    for metric in metrics:
        local_aliases: set[str] = set()
        for alias in metric.aliases:
            alias_key = _exact_key(alias)
            if alias_key in local_aliases:
                raise OntologyCatalogError(
                    f"Metric {metric.current_metric_id} 内 alias 重复：{alias}"
                )
            local_aliases.add(alias_key)
            owner = aliases.get(alias_key)
            if owner is not None and owner != metric.current_metric_id:
                raise OntologyCatalogError(f"Metric alias 冲突：{alias}")
            formal_owner = names.get(alias_key)
            if formal_owner is not None and formal_owner != metric.current_metric_id:
                raise OntologyCatalogError(
                    f"Metric alias 与正式名称冲突：{alias}"
                )
            aliases[alias_key] = metric.current_metric_id


def _expect_spec(
    properties: Mapping[str, Any],
    name: str,
    expected_type: str,
    target_field: str | None = None,
    expected_target: str | None = None,
) -> None:
    spec = _mapping(properties.get(name), name)
    if spec.get("type") != expected_type:
        raise OntologyCatalogError(
            f"{name}.type 应为 {expected_type}，实际为 {spec.get('type')}"
        )
    if target_field and spec.get(target_field) != expected_target:
        raise OntologyCatalogError(
            f"{name}.{target_field} 应为 {expected_target}，"
            f"实际为 {spec.get(target_field)}"
        )


def _schema_field(name: str, raw_spec: Any) -> SchemaField:
    spec = _mapping(raw_spec, name)
    value_type = str(spec.get("type") or "")
    target_field = {
        "reference": "reference",
        "struct": "struct",
        "enum": "enum",
    }.get(value_type)
    target = str(spec.get(target_field)) if target_field else None
    return SchemaField(
        name=name,
        value_type=value_type,
        target=target,
        required=spec.get("required") is True,
    )


def _exact_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()
