from django.urls import path
from . import views

urlpatterns = [
    path('upload/', views.upload_view, name='upload'),
    path('evaluate/', views.evaluate_view, name='evaluate'),
    path('result/<uuid:job_id>/', views.result_view, name='result'),
]
