from django.urls import path

from . import views

app_name = "accessories"

urlpatterns = [
    path("", views.AccessoryListView.as_view(), name="list"),
    path("create/", views.AccessoryCreateView.as_view(), name="create"),
    path("<int:pk>/", views.AccessoryDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.AccessoryUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.AccessoryDeleteView.as_view(), name="delete"),
]
