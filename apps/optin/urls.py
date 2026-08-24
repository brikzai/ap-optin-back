from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("optins", views.optins_collection),
    path("optins/<str:optin_id>", views.optin_detail),
]
