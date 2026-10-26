"""
Unified Pipeline for Assessment CSV Ingestion and Adaptive Model Processing (IRT + BKT).
"""

import logging
from typing import Dict, Any, Optional

from app.ingestion.service import IngestionService
from app.irt.service import IRTService
from app.bkt.services import BKTService

logger = logging.getLogger(__name__)


class AdaptivePipeline:
    """
    Coordinates the full Objective 1 workflow:
    CSV upload/import -> persistence in Supabase -> Rasch IRT calibration -> BKT mastery estimation.
    """

    @classmethod
    def run_pipeline(
        cls,
        csv_input,
        filename: Optional[str] = None,
        item_scope: str = "first_attempt",
        bkt_unanswered_policy: str = "skip",
        bkt_use_irt_prior: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute full pipeline:
        1. Ingest & validate CSV responses (idempotent, transactional).
        2. Calibrate Item Difficulties and estimate Student Ability (IRT Rasch 1PL).
        3. Estimate Concept-Level Mastery (Bayesian Knowledge Tracing).
        4. Return unified execution report.
        """
        logger.info("Starting Adaptive Pipeline execution...")

        # 1. CSV Ingestion
        ingestion_result = IngestionService.import_csv(csv_input, filename=filename)

        # If duplicate and already processed, we can still re-verify models or report
        logger.info(f"Ingestion completed with status: {ingestion_result['status']}")

        # 2. IRT Calibration and Estimation
        irt_result = IRTService.recalculate_all(item_scope=item_scope)
        logger.info(f"IRT completed: {irt_result['items_calibrated']} items calibrated, "
                    f"{irt_result['students_estimated']} students estimated.")

        # 3. BKT Mastery Estimation
        bkt_result = BKTService.recalculate_all(
            unanswered_policy=bkt_unanswered_policy,
            use_irt_prior=bkt_use_irt_prior,
        )
        logger.info(f"BKT completed: {bkt_result['student_concepts_updated']} student-concept pairs updated.")

        return {
            "status": "SUCCESS",
            "ingestion": ingestion_result,
            "irt": irt_result,
            "bkt": bkt_result,
        }

    @classmethod
    def recalculate_models_only(
        cls,
        item_scope: str = "first_attempt",
        bkt_unanswered_policy: str = "skip",
        bkt_use_irt_prior: bool = False,
    ) -> Dict[str, Any]:
        """
        Recalculate both IRT and BKT models on existing persistent imported responses
        without re-uploading or altering the raw imported response records.
        """
        irt_result = IRTService.recalculate_all(item_scope=item_scope)
        bkt_result = BKTService.recalculate_all(
            unanswered_policy=bkt_unanswered_policy,
            use_irt_prior=bkt_use_irt_prior,
        )
        return {
            "status": "SUCCESS",
            "irt": irt_result,
            "bkt": bkt_result,
        }
