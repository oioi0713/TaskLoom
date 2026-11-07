"""Structured payloads available to workers through output contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.types import NonEmptyStr


class Source(BaseModel):
    """Evidence attached to a structured worker result."""

    model_config = ConfigDict(extra="forbid")

    source_id: NonEmptyStr
    title: NonEmptyStr
    url: str | None = None
    document_id: str | None = None
    published_at: str | None = None
    accessed_at: str | None = None
    excerpt: str = ""
    source_type: Literal[
        "official_doc",
        "paper",
        "web_page",
        "local_document",
    ] | None = None


class ResearchResult(BaseModel):
    """Findings produced by a research worker."""

    model_config = ConfigDict(extra="forbid")

    summary: NonEmptyStr
    findings: list[NonEmptyStr] = Field(min_length=1)
    sources: list[Source] = Field(default_factory=list)


class ComparisonOption(BaseModel):
    """One option evaluated by a comparison worker."""

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyStr
    strengths: list[NonEmptyStr] = Field(default_factory=list)
    weaknesses: list[NonEmptyStr] = Field(default_factory=list)


class ComparisonResult(BaseModel):
    """Structured comparison across two or more alternatives."""

    model_config = ConfigDict(extra="forbid")

    summary: NonEmptyStr
    criteria: list[NonEmptyStr] = Field(min_length=1)
    options: list[ComparisonOption] = Field(min_length=2)
    recommendation: NonEmptyStr


class ExperimentPlanResult(BaseModel):
    """A reproducible experiment proposal."""

    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyStr
    hypothesis: NonEmptyStr
    steps: list[NonEmptyStr] = Field(min_length=1)
    metrics: list[NonEmptyStr] = Field(min_length=1)
    risks: list[NonEmptyStr] = Field(default_factory=list)


class ReportSection(BaseModel):
    """One section of a report result."""

    model_config = ConfigDict(extra="forbid")

    heading: NonEmptyStr
    content: NonEmptyStr


class ReportResult(BaseModel):
    """A long-form report assembled from reviewed worker results."""

    model_config = ConfigDict(extra="forbid")

    title: NonEmptyStr
    executive_summary: NonEmptyStr
    sections: list[ReportSection] = Field(min_length=1)
