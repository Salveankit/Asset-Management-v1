from django.conf import settings


def product_settings(request):
    return {
        "product_name": settings.PRODUCT_NAME,
        "product_short_name": settings.PRODUCT_SHORT_NAME,
        "company_name": settings.COMPANY_NAME,
        "product_logo": settings.PRODUCT_LOGO,
    }
