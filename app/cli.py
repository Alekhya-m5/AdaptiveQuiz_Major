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

    args = parser.parse_args()
    app = create_app()

    with app.app_context():
        if args.command == "import-csv":
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
