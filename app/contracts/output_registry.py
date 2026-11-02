"""Registry of supported structured worker output contracts."""

from types import MappingProxyType
from typing import Literal, Mapping

from pydantic import BaseModel

from app.contracts.output_models import (
    ComparisonResult,
    ExperimentPlanResult,
    ReportResult,
    ResearchResult,
)

OutputContractName = Literal[
    "research_result_v1",
    "comparison_result_v1",
    "experiment_plan_v1",
    "report_result_v1",
]

OUTPUT_CONTRACTS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        "research_result_v1": ResearchResult,
        "comparison_result_v1": ComparisonResult,
        "experiment_plan_v1": ExperimentPlanResult,
        "report_result_v1": ReportResult,
    }
)


def has_output_contract(name: str) -> bool:
    """Return whether an output contract is registered."""

    return name in OUTPUT_CONTRACTS


def validate_output(contract_name: str, payload: dict) -> BaseModel:
    """Validate a payload with the selected registered output model."""

    try:
        model = OUTPUT_CONTRACTS[contract_name]
    except KeyError as exc:
        raise ValueError(f"Unknown output contract: {contract_name}") from exc
    return model.model_validate(payload)
