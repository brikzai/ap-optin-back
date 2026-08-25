from django.urls import path, include

urlpatterns = [
    path("api/v1/", include("apps.optin.urls")),
    path("api/v1/", include("apps.cliente.urls")),
]
