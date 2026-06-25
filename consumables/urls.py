from django.urls import path

from . import views

app_name = "consumables"

urlpatterns = [
    path("", views.ConsumableListView.as_view(), name="list"),
    path("create/", views.ConsumableCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ConsumableDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ConsumableUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.ConsumableDeleteView.as_view(), name="delete"),
]
