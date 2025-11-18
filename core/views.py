import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import UploadedDocument, EvaluationJob
from .tasks import evaluate_job_task

@csrf_exempt
def upload_view(request):
    """
    POST multipart/form-data with fields 'cv' and 'report' (PDF files).
    Returns: { cv_id, report_id }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'use POST'}, status=405)

    def _is_pdf(f):
        if not f:
            return False
        if getattr(f, 'content_type', '') == 'application/pdf':
            return True
        return f.name.lower().endswith('.pdf')

    cv = request.FILES.get('cv')
    report = request.FILES.get('report')
    if not cv or not report:
        return JsonResponse({'error': 'cv and report files required'}, status=400)
    if not _is_pdf(cv) or not _is_pdf(report):
        return JsonResponse({'error': 'only PDF uploads are supported'}, status=400)

    cv_obj = UploadedDocument.objects.create(file=cv, original_name=cv.name)
    report_obj = UploadedDocument.objects.create(file=report, original_name=report.name)

    return JsonResponse({'cv_id': str(cv_obj.id), 'report_id': str(report_obj.id)})

@csrf_exempt
def evaluate_view(request):
    """
    POST JSON: { job_title, cv_id, report_id } -> enqueue async evaluation
    Returns: { id, status: 'queued' }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'use POST'}, status=405)

    try:
        body = json.loads(request.body.decode())
    except Exception:
        return JsonResponse({'error': 'invalid json'}, status=400)

    job_title = body.get('job_title')
    cv_id = body.get('cv_id')
    report_id = body.get('report_id')

    if not (job_title and cv_id and report_id):
        return JsonResponse({'error': 'job_title, cv_id, report_id required'}, status=400)

    try:
        cv = UploadedDocument.objects.get(id=cv_id)
        report = UploadedDocument.objects.get(id=report_id)
    except UploadedDocument.DoesNotExist:
        return JsonResponse({'error': 'document not found'}, status=404)

    job = EvaluationJob.objects.create(job_title=job_title, cv=cv, report=report, status='queued')
    # enqueue Celery task (non-blocking)
    evaluate_job_task.delay(str(job.id))

    return JsonResponse({'id': str(job.id), 'status': 'queued'}, status=202)

def result_view(request, job_id):
    """
    GET /result/<job_id> returns job status or final result.
    """
    try:
        job = EvaluationJob.objects.get(id=job_id)
    except EvaluationJob.DoesNotExist:
        return JsonResponse({'error': 'job_not_found'}, status=404)

    if job.status in ['queued', 'processing']:
        return JsonResponse({'id': str(job.id), 'status': job.status})
    if job.status == 'failed':
        return JsonResponse({'id': str(job.id), 'status': job.status, 'error': job.error}, status=500)
    return JsonResponse({'id': str(job.id), 'status': 'completed', 'result': job.result})
