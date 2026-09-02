from django.urls import path

from . import views

urlpatterns = [
    path("clientes", views.clientes_collection),
    path("clientes/<str:cliente_id>", views.cliente_detail),
]
