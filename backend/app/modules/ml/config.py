from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.ingestion.history import local_time


class TrainingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    seed: int = Field(default=20251009, ge=0, le=2**31 - 1)
    warmup_end: str = "2025-01-15"
    train_end: str = "2025-02-08"
    selection_end: str = "2025-02-13"
    calibration_end: str = "2025-02-18"
    test_end: str = "2025-03-01"
    history_delay_minutes: int = Field(default=1440, ge=0, le=100000)
    threads: int = Field(default=2, ge=1, le=4)
    trees: int = Field(default=180, ge=10, le=400)
    num_leaves: int = Field(default=15, ge=4, le=31)
    min_data_in_leaf: int = Field(default=100, ge=10)
    learning_rate: float = Field(default=0.05, gt=0, le=0.2)
    interval_coverage: float = Field(default=0.9, ge=0.5, le=0.99)
    maximum_train_rows: int = Field(default=300000, ge=100, le=400000)
    acceptance_improvement: float = Field(default=0.01, ge=0, le=0.5)

    @model_validator(mode="after")
    def chronological(self) -> TrainingConfig:
        dates = [
            local_time(getattr(self, name))
            for name in ("warmup_end", "train_end", "selection_end", "calibration_end", "test_end")
        ]
        if any(a >= b for a, b in zip(dates, dates[1:], strict=False)):
            raise ValueError("Splits must be strictly chronological")
        return self

    def boundaries(self) -> list[int]:
        return [
            int(local_time(getattr(self, name)).timestamp())
            for name in ("warmup_end", "train_end", "selection_end", "calibration_end", "test_end")
        ]
