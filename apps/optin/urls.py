from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("optins", views.optins_collection),
]
