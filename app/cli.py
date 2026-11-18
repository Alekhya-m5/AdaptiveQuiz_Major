"""
Command-Line Interface for Adaptive Assessment Pipeline.
Supports Flask CLI and direct python execution: python -m app.cli <command>
"""

import argparse
import json
import sys
from pathlib import Path

from app import create_app
from app.ingestion.service import IngestionService
from app.pipeline import AdaptivePipeline
from app.models import ImportBatch, ItemCalibration, StudentTheta, StudentMastery, ImportedResponse


def import_all_assessments(data_dir: Path = None):
    """
    Discovers and imports all assessment CSVs from data/assessments/,
    then recalculates IRT and BKT models end-to-end.
    Safe and idempotent: skips files already imported.
    """
    if data_dir is None:
        data_dir = Path(__file__).resolve().parent.parent / "data" / "assessments"

    if not data_dir.exists():
        print(f"Data directory '{data_dir}' does not exist.")
        return {"status": "ERROR", "message": f"Directory not found: {data_dir}"}

    csv_files = sorted(list(data_dir.glob("*.csv")))
    if not csv_files:
        print(f"No CSV files found in '{data_dir}'.")
        return {"status": "NO_FILES", "files": []}

    print(f"\nDiscovered {len(csv_files)} assessment file(s) in {data_dir}:")
    for f in csv_files:
        print(f" - {f.name}")

    results = []
    for csv_file in csv_files:
        print(f"\nProcessing '{csv_file.name}'...")
        res = IngestionService.import_csv(str(csv_file))
        status = res.get("status")
        if status == "SUCCESS":
            print(f"  [SUCCESS] Imported {res.get('valid_rows')} rows ({res.get('student_count')} students, {res.get('question_count')} questions).")
        elif status == "DUPLICATE":
            print(f"  [SKIPPED] Already imported in batch #{res.get('batch_id')} (Idempotent).")
        else:
            print(f"  [{status}] {res.get('message', '')}")
        results.append(res)

    print("\nRecalculating IRT and BKT models on all cumulative responses...")
    model_res = AdaptivePipeline.recalculate_models_only()
    print(f"  IRT: {model_res['irt']['items_calibrated']} items calibrated across {model_res['irt']['students_estimated']} students.")
    print(f"  BKT: {model_res['bkt']['student_concepts_updated']} student-concept mastery pairs updated.")

    batches = ImportBatch.query.all()
    responses = ImportedResponse.query.count()
    calibrations = ItemCalibration.query.all()
    thetas = StudentTheta.query.all()
    masteries = StudentMastery.query.all()

    print("\n================ ASSESSMENT INITIALIZATION COMPLETE ================")
    print(f"Total Import Batches: {len(batches)}")
    for b in batches:
        print(f"  Batch #{b.id}: {b.filename} | Status: {b.status} | Rows: {b.valid_rows}/{b.total_rows}")
    print(f"Total Imported Responses: {responses}")
    print(f"Total Calibrated Items: {len(calibrations)}")
    print(f"Total Students with Theta: {len(thetas)}")
    print(f"Total Student-Concept Mastery Records: {len(masteries)}")
    print("====================================================================\n")

    return {
        "status": "SUCCESS",
        "files_processed": len(csv_files),
        "ingestion_results": results,
        "models": model_res,
    }


def register_cli_commands(app):
    """Register CLI commands with Flask app."""

    @app.cli.command("import-csv")
    def cli_import_csv():
        """Import a weekly assessment CSV file."""
        if len(sys.argv) < 3:
            print("Usage: flask import-csv <path-to-csv>")
            sys.exit(1)
        csv_path = sys.argv[2]
        res = IngestionService.import_csv(csv_path)
        print(json.dumps(res, indent=2, default=str))

    @app.cli.command("import-all-assessments")
    def cli_import_all_assessments():
        """Import all assessment CSVs from data/assessments/ and recalculate IRT/BKT models."""
        custom_dir = sys.argv[2] if len(sys.argv) >= 3 else None
        dir_path = Path(custom_dir) if custom_dir else None
        import_all_assessments(dir_path)

    @app.cli.command("recalculate-models")
    def cli_recalculate_models():
        """Recalculate IRT item difficulties, theta estimates, and BKT mastery."""
        res = AdaptivePipeline.recalculate_models_only()
        print(json.dumps(res, indent=2, default=str))

    @app.cli.command("run-pipeline")
    def cli_run_pipeline():
        """Run the full end-to-end pipeline on a CSV file."""
        if len(sys.argv) < 3:
            print("Usage: flask run-pipeline <path-to-csv>")
            sys.exit(1)
        csv_path = sys.argv[2]
        res = AdaptivePipeline.run_pipeline(csv_path)
        print(json.dumps(res, indent=2, default=str))

    @app.cli.command("show-summary")
    def cli_show_summary():
        """Display summary statistics from database."""
        batches = ImportBatch.query.all()
        responses = ImportedResponse.query.count()
        calibrations = ItemCalibration.query.all()
        thetas = StudentTheta.query.all()
        masteries = StudentMastery.query.all()

        print("\n================ ADAPTIVE PIPELINE DATABASE SUMMARY ================")
        print(f"Total Import Batches: {len(batches)}")
        for b in batches:
            print(f"  Batch #{b.id}: {b.filename} | Status: {b.status} | Rows: {b.valid_rows}/{b.total_rows}")

        print(f"\nTotal Imported Responses: {responses}")
        print(f"Total Calibrated Items: {len(calibrations)}")
        if calibrations:
            easy_item = min(calibrations, key=lambda c: c.difficulty_b)
            hard_item = max(calibrations, key=lambda c: c.difficulty_b)
            print(f"  Easiest: {easy_item.question_id} (b={easy_item.difficulty_b:.4f}, {easy_item.concept_tag})")
            print(f"  Hardest: {hard_item.question_id} (b={hard_item.difficulty_b:.4f}, {hard_item.concept_tag})")

        print(f"\nTotal Students with Theta: {len(thetas)}")
        if thetas:
            avg_theta = sum(t.theta for t in thetas) / len(thetas)
            print(f"  Average Theta: {avg_theta:.4f}")

        print(f"\nTotal Student-Concept Mastery Records: {len(masteries)}")
        print("===================================================================\n")


def main():
    """Standalone CLI entry point."""
    parser = argparse.ArgumentParser(description="Adaptive Quiz Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # import-csv
    p_import = subparsers.add_parser("import-csv", help="Import a weekly assessment CSV")
    p_import.add_argument("csv_path", type=str, help="Path to CSV file")

    # recalculate-models
    subparsers.add_parser("recalculate-models", help="Recalculate IRT and BKT models")

    # run-pipeline
    p_pipe = subparsers.add_parser("run-pipeline", help="Run full pipeline on CSV")
    p_pipe.add_argument("csv_path", type=str, help="Path to CSV file")
    p_pipe.add_argument("--scope", type=str, default="first_attempt", choices=["first_attempt", "all_attempts"])

    # show-summary
    subparsers.add_parser("show-summary", help="Show database summary statistics")

    # import-all-assessments
    p_all = subparsers.add_parser("import-all-assessments", help="Import all assessment CSVs from data/assessments/")
    p_all.add_argument("--dir", type=str, default=None, help="Optional custom data directory")

    args = parser.parse_args()
    app = create_app()

    with app.app_context():
        if args.command == "import-all-assessments":
            dir_path = Path(args.dir) if args.dir else None
            import_all_assessments(dir_path)
        elif args.command == "import-csv":
            res = IngestionService.import_csv(args.csv_path)
            print(json.dumps(res, indent=2, default=str))
        elif args.command == "recalculate-models":
            res = AdaptivePipeline.recalculate_models_only()
            print(json.dumps(res, indent=2, default=str))
        elif args.command == "run-pipeline":
            res = AdaptivePipeline.run_pipeline(args.csv_path, item_scope=args.scope)
            print(json.dumps(res, indent=2, default=str))
        elif args.command == "show-summary":
            batches = ImportBatch.query.all()
            responses = ImportedResponse.query.count()
            calibrations = ItemCalibration.query.all()
            thetas = StudentTheta.query.all()
            masteries = StudentMastery.query.all()
            print("\n================ ADAPTIVE PIPELINE DATABASE SUMMARY ================")
            print(f"Total Import Batches: {len(batches)}")
            for b in batches:
                print(f"  Batch #{b.id}: {b.filename} | Status: {b.status} | Rows: {b.valid_rows}/{b.total_rows}")
            print(f"Total Imported Responses: {responses}")
            print(f"Total Calibrated Items: {len(calibrations)}")
            print(f"Total Students with Theta: {len(thetas)}")
            print(f"Total Student-Concept Mastery Records: {len(masteries)}")
            print("===================================================================\n")


if __name__ == "__main__":
    main()
