"""
Smoke test for CSV ingestion processor.
"""

from pathlib import Path
from app.ingestion.processor import process_weekly_csv

PROJECT_ROOT = Path(__file__).resolve().parent
FILE_PATH = PROJECT_ROOT / "data" / "assessments" / "Unit-1_Quiz_02-09-2026_Detailed_Results.csv"
if not FILE_PATH.exists():
    FILE_PATH = Path.home() / "Downloads" / "Unit-1_Quiz_02-09-2026_Detailed_Results.csv"

if __name__ == "__main__":
    if FILE_PATH.exists():
        result = process_weekly_csv(str(FILE_PATH))
        print("\n================================")
        print("CSV INGESTION SUCCESSFUL")
        print("================================")
        print("Total rows:", result["total_rows"])
        print("Valid records:", result["valid_rows"])
        print("Students:", result["student_count"])
        print("Questions:", result["question_count"])
        print("\nFirst record sample:")
        for k, v in list(result["records"][0].items())[:10]:
            print(f"  {k}: {v}")
    else:
        print(f"File not found: {FILE_PATH}")