from __future__ import annotations

import re
from typing import List, Union
from pydantic import BaseModel, Field


def clean_percentage(val: Union[float, int, str]) -> float:
    """Normalize percentage values from strings like '82.5%' or floats into standard float."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        match = re.search(r"[-+]?\d*\.?\d+", val)
        if match:
            return float(match.group(0))
    return 0.0


class SubjectAttendance(BaseModel):
    """Details for a single subject's attendance."""
    name: str = Field(description="Name or course code of the subject")
    percentage: Union[float, str] = Field(description="Attendance percentage, e.g. 85.5 or '85.5%'")
    attended: Union[int, str] = Field(description="Number of classes attended")
    total: Union[int, str] = Field(description="Total number of classes conducted")

    @property
    def numeric_percentage(self) -> float:
        """Returns float representation of percentage."""
        return clean_percentage(self.percentage)


class AttendanceSummary(BaseModel):
    """Overall summary of attendance extracted from the period-wise page."""
    overall_percentage: Union[float, str] = Field(description="Overall percentage across all classes")
    total_classes: Union[int, str] = Field(default=0, description="Total classes conducted")
    attended: Union[int, str] = Field(default=0, description="Total classes attended")
    absent: Union[int, str] = Field(default=0, description="Total classes absent or missed")
    subjects: List[SubjectAttendance] = Field(default_factory=list, description="List of per-subject attendance")
    low_attendance_subjects: List[str] = Field(
        default_factory=list,
        description="List of subjects having attendance strictly under 75%"
    )
    one_line_summary: str = Field(
        default="No summary available.",
        description="Short and punchy status line summarizing the attendance"
    )

    @property
    def numeric_overall_percentage(self) -> float:
        """Returns float representation of overall percentage."""
        return clean_percentage(self.overall_percentage)
