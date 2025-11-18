import json
import time
from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone
from .models import EvaluationJob
from .utils import extract_text_from_pdf
from .vector_client import VectorClient
from .llm import LLMClient
from .scoring import computeCvMatchRate, computeProjectScore

logger = get_task_logger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def evaluate_job_task(self, job_id):
    """
    Celery task that runs the end-to-end evaluation pipeline:
    - Extract text from uploaded PDFs
    - Ensure system docs are ingested into the vector DB
    - RAG retrieval for job description and rubrics
    - Parse CV and project report using LLM
    - Score both using LLM, compute aggregated scores
    - Synthesize final summary
    """
    try:
        job = EvaluationJob.objects.get(id=job_id)
        job.status = 'processing'
        job.save()

        # 1) load file texts
        cv_path = job.cv.file.path
        report_path = job.report.file.path
        cv_text = extract_text_from_pdf(cv_path)
        report_text = extract_text_from_pdf(report_path)

        # 2) vector DB ingestion (idempotent)
        vc = VectorClient()
        vc.ingest_system_docs_if_needed()

        # 3) RAG retrieval
        jd_passages = vc.query_relevant(job.job_title, collection='job_descriptions', top_k=4)
        cv_rubric = vc.query_relevant('cv scoring rubric', collection='scoring_rubrics', top_k=4)

        # 4) parse CV
        llm = LLMClient()
        cv_struct = llm.parse_resume_to_json(cv_text or "")

        # 5) CV scoring
        cv_score_json = llm.score_cv(cv_struct, jd_passages, cv_rubric)
        # ensure keys exist and normalized to numbers where possible
        # LLM might return floats or strings; convert if possible
        try:
            # naive conversion
            for k in ['technical_skills','experience_level','achievements','cultural_fit']:
                if k in cv_score_json:
                    cv_score_json[k] = float(cv_score_json[k])
        except Exception:
            logger.warning("Could not coerce cv score types; leaving as-is")

        # 6) project parsing + scoring
        case_passages = vc.query_relevant('case study brief', collection='case_study', top_k=4)
        project_rubric = vc.query_relevant('project scoring rubric', collection='scoring_rubrics', top_k=4)
        project_struct = llm.parse_project_report_to_json(report_text or "")
        project_score_json = llm.score_project(project_struct, case_passages, project_rubric)
        try:
            for k in ['correctness','code_quality','resilience','documentation','creativity']:
                if k in project_score_json:
                    project_score_json[k] = float(project_score_json[k])
        except Exception:
            logger.warning("Could not coerce project score types; leaving as-is")

        # 7) aggregated numeric scores
        cv_match_rate = computeCvMatchRate(cv_score_json or {})
        project_score = computeProjectScore(project_score_json or {})

        # 8) final synthesis
        final = llm.synthesize_final(cv_score_json or {}, project_score_json or {}, cv_match_rate, project_score)

        result = {
            'cv_match_rate': cv_match_rate,
            'cv_scores': cv_score_json,
            'cv_feedback': (cv_score_json.get('feedback') if isinstance(cv_score_json, dict) else ""),
            'project_score': project_score,
            'project_breakdown': project_score_json,
            'project_feedback': (project_score_json.get('feedback') if isinstance(project_score_json, dict) else ""),
            'overall_summary': final.get('overall_summary', '') if isinstance(final, dict) else '',
            'recommendation': final.get('recommendation', 'hold') if isinstance(final, dict) else 'hold'
        }

        job.result = result
        job.status = 'completed'
        job.finished_at = timezone.now()
        job.save()
    except Exception as exc:
        logger.exception("Evaluation job failed")
        try:
            job = EvaluationJob.objects.get(id=job_id)
            job.status = 'failed'
            job.error = str(exc)
            job.finished_at = timezone.now()
            job.save()
        except Exception:
            logger.exception("Failed to mark job as failed in DB")
        # retry via Celery
        raise self.retry(exc=exc)
