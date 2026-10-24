"""
test_demo.py - Quickly test and preview the Rich terminal output
without needing to launch a browser or connect to the portal.
"""
from agent.models import AttendanceSummary, SubjectAttendance
from agent.cli import render_attendance_summary

def run_demo() -> None:
    sample = AttendanceSummary(
        overall_percentage=82.5,
        total_classes=118,
        attended=97,
        absent=21,
        subjects=[
            SubjectAttendance(name="Database Management Systems", percentage=88.5, attended=46, total=52),
            SubjectAttendance(name="Operating Systems", percentage=82.0, attended=41, total=50),
            SubjectAttendance(name="Computer Networks", percentage=68.7, attended=11, total=16),
        ],
        low_attendance_subjects=["Computer Networks"],
        one_line_summary="Attendance is overall in good standing at 82.5%, but Computer Networks requires attention (<75%).",
    )
    render_attendance_summary(sample)

if __name__ == "__main__":
    run_demo()
