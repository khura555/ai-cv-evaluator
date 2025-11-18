from django.db import models
import uuid

def upload_path(instance, filename):
    # store uploads with a random prefix to avoid collisions
    return f"{uuid.uuid4().hex}_{filename}"

class UploadedDocument(models.Model):
    """
    Stores uploaded candidate files (CV, project report).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to=upload_path)
    original_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.original_name} ({self.id})"

class EvaluationJob(models.Model):
    """
    Represents an asynchronous evaluation job.
    """
    JOB_STATUS = [
        ('queued', 'queued'),
        ('processing', 'processing'),
        ('completed', 'completed'),
        ('failed', 'failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_title = models.CharField(max_length=255)
    cv = models.ForeignKey(UploadedDocument, related_name='cv_jobs', on_delete=models.CASCADE)
    report = models.ForeignKey(UploadedDocument, related_name='report_jobs', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=JOB_STATUS, default='queued')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Job {self.id} [{self.status}]"
