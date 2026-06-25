def filter_for_user_company(queryset, user, field_name: str = "company_id"):
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user.is_staff or user.is_superuser or not getattr(user, "company_id", None):
        return queryset
    return queryset.filter(**{field_name: user.company_id})
