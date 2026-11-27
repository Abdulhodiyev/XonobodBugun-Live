from typing import Literal
from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    mahalla_id: str = Field(..., min_length=2, max_length=60)
    utility_type: Literal["power", "water", "gas"]
    issue_subtype: Literal["outage", "low_pressure"] = "outage"
    street_hint: str = Field(default="", max_length=160)


class ResolveRequest(BaseModel):
    mahalla_id: str = Field(..., min_length=2, max_length=60)
    utility_type: Literal["power", "water", "gas"]


class StationVoteRequest(BaseModel):
    station_id: str = Field(..., min_length=2, max_length=60)
    status: Literal["open", "queue", "closed", "long_queue"]