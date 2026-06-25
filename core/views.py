from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Q


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class SearchableListMixin:
    search_fields: tuple[str, ...] = ()

    def get_search_query(self) -> str:
        return self.request.GET.get("q", "").strip()

    def get_queryset(self):
        queryset = super().get_queryset()
        query = self.get_search_query()
        if query and self.search_fields:
            predicate = Q()
            for field in self.search_fields:
                predicate |= Q(**{f"{field}__icontains": query})
            queryset = queryset.filter(predicate)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.get_search_query()
        return context
