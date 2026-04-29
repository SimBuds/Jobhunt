"""Pydantic domain models. Extended in later phases."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RemoteType = Literal["onsite", "hybrid", "remote", "unknown"]
ApplicationStatus = Literal[
    "drafted", "applied", "interviewing", "offer", "rejected", "withdrawn"
]


class Job(BaseModel):
    id: str
    source: str
    external_id: str
    company: str | None = None
    title: str | None = None
    location: str | None = None
    remote_type: RemoteType = "unknown"
    description: str | None = None
    url: str | None = None
    posted_at: datetime | None = None
    ingested_at: datetime | None = None
    raw_json: str | None = None


class Company(BaseModel):
    name: str
    homepage: str | None = None
    notes: str | None = None


class Score(BaseModel):
    job_id: str
    score: int
    reasons: list[str] = []
    red_flags: list[str] = []
    must_clarify: list[str] = []
    model: str | None = None
    prompt_hash: str | None = None
    scored_at: datetime | None = None


class Application(BaseModel):
    id: str
    job_id: str
    status: ApplicationStatus = "drafted"
    resume_path: str | None = None
    cover_path: str | None = None
    fill_plan_path: str | None = None
    applied_at: datetime | None = None
    notes: str | None = None
