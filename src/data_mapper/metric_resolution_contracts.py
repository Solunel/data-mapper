"""Metric Resolution 的生产数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .observation_contracts import Evidence, JsonContract, MetricSubject, ObservationSchema


class MetricMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"
    ONTOLOGY_GAP = "ONTOLOGY_GAP"


class MetricResolutionMode(str, Enum):
    DETERMINISTIC_ONLY = "DETERMINISTIC_ONLY"
    DETERMINISTIC_WITH_SEMANTIC_FALLBACK = "DETERMINISTIC_WITH_SEMANTIC_FALLBACK"


@dataclass(frozen=True)
class MetricResolutionRequest(JsonContract):
    mode: MetricResolutionMode = MetricResolutionMode.DETERMINISTIC_ONLY
    metric_overrides: Mapping[int, str] = field(default_factory=dict)
    ontology_gap_confirmations: tuple[int, ...] = ()
    deterministic_rule_version: str = "deterministic-resolution-v1"
    retrieval_top_k: int = 5
    semantic_context_window: int = 2


@dataclass(frozen=True)
class OntologyMetric(JsonContract):
    current_metric_id: str
    name_cn: str
    aliases: tuple[str, ...]
    definition_cn: str
    business_labels: tuple[str, ...]
    value_semantics: str | None
    status: str
    version: str


@dataclass(frozen=True)
class OntologyCatalogSummary(JsonContract):
    ontology_revision: str
    metric_count: int
    organization_count: int
    period_basis_values: tuple[str, ...]
    unit_values: tuple[str, ...]


@dataclass(frozen=True)
class OntologyCatalog(JsonContract):
    """Mapping Core 唯一依赖的只读本体值对象。"""

    ontology_revision: str
    observation_schema: ObservationSchema
    organization_ids: tuple[str, ...]
    metrics: tuple[OntologyMetric, ...]

    @property
    def actual_observation_required_fields(self) -> tuple[str, ...]:
        return self.observation_schema.actual_observation_required_fields

    @property
    def period_required_fields(self) -> tuple[str, ...]:
        return self.observation_schema.period_required_fields

    @property
    def period_basis_values(self) -> tuple[str, ...]:
        return self.observation_schema.period_basis_values

    @property
    def period_type_values(self) -> tuple[str, ...]:
        return self.observation_schema.period_type_values

    @property
    def unit_values(self) -> Mapping[str, str]:
        return self.observation_schema.unit_values

    def metric_by_id(self, current_metric_id: str) -> OntologyMetric | None:
        return next(
            (
                metric
                for metric in self.metrics
                if metric.current_metric_id == current_metric_id
            ),
            None,
        )

    def summary(self) -> OntologyCatalogSummary:
        return OntologyCatalogSummary(
            ontology_revision=self.ontology_revision,
            metric_count=len(self.metrics),
            organization_count=len(self.organization_ids),
            period_basis_values=self.period_basis_values,
            unit_values=tuple(self.unit_values),
        )


@dataclass(frozen=True)
class MetricDecision(JsonContract):
    decision_id: str
    subject: MetricSubject
    status: MetricMatchStatus
    ontology_revision: str
    selected_metric: OntologyMetric | None
    candidates: tuple[OntologyMetric, ...]
    evidence: tuple[Evidence, ...]
    ontology_gap_candidate: bool = False
