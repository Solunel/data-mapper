"""企业表格治理与只读本体 Mapping 的公开接口。"""

from .contracts import PipelineConfig, Phase1Result
from .errors import InputParseError, Phase1Error, UnsupportedFormatError
from .mapping import comparison_key, comparison_name, map_curated_dataset
from .mapping_contracts import (
    Evidence,
    MappingPlan,
    MappingRequest,
    MappingReport,
    MappingResult,
    MetricDecision,
    MetricMatchStatus,
    ObservationCandidate,
    OntologyCatalog,
    OntologyMetric,
    RowRole,
    ScalarBinding,
    StructureStatus,
    TableMappingPlan,
    ValueFieldBinding,
)
from .ontology_catalog import OntologyCatalogError, load_ontology_catalog
from .pipeline import curate_file

__all__ = [
    "Evidence",
    "InputParseError",
    "MappingPlan",
    "MappingRequest",
    "MappingReport",
    "MappingResult",
    "MetricDecision",
    "MetricMatchStatus",
    "ObservationCandidate",
    "OntologyCatalog",
    "OntologyCatalogError",
    "OntologyMetric",
    "RowRole",
    "Phase1Error",
    "Phase1Result",
    "PipelineConfig",
    "ScalarBinding",
    "StructureStatus",
    "TableMappingPlan",
    "UnsupportedFormatError",
    "ValueFieldBinding",
    "comparison_key",
    "comparison_name",
    "curate_file",
    "load_ontology_catalog",
    "map_curated_dataset",
]
