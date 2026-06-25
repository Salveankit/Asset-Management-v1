from django import template

register = template.Library()


@register.filter
def attr(obj, field_name):
    return getattr(obj, field_name)


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return ""
    return mapping.get(key, "")
