from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(
    DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["127.0.0.1", "localhost"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    PRODUCT_NAME=(str, "Asset Platform"),
    PRODUCT_SHORT_NAME=(str, "AssetOps"),
    COMPANY_NAME=(str, "Organisation Workspace"),
    PRODUCT_LOGO=(str, ""),
    CELERY_BROKER_URL=(str, "redis://127.0.0.1:6379/0"),
    CELERY_RESULT_BACKEND=(str, "redis://127.0.0.1:6379/1"),
    AZURE_OPENAI_ENDPOINT=(str, ""),
    AZURE_OPENAI_API_KEY=(str, ""),
    AZURE_OPENAI_DEPLOYMENT=(str, ""),
    AZURE_OPENAI_API_VERSION=(str, ""),
    AZURE_OPENAI_TIMEOUT_SECONDS=(int, 30),
    AZURE_OPENAI_MAX_RETRIES=(int, 3),
)
local_env = BASE_DIR / ".env.local"
if local_env.exists():
    environ.Env.read_env(local_env, overwrite=True)

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-phase-1-bootstrap-only-change-me",
)
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

PRODUCT_NAME = env("PRODUCT_NAME")
PRODUCT_SHORT_NAME = env("PRODUCT_SHORT_NAME")
COMPANY_NAME = env("COMPANY_NAME")
PRODUCT_LOGO = env("PRODUCT_LOGO")

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'core',
    'accounts',
    'organisations',
    'people',
    'assets',
    'catalogue',
    'locations',
    'suppliers',
    'licences',
    'accessories',
    'consumables',
    'components',
    'checkouts',
    'audits',
    'maintenance',
    'imports',
    'reports',
    'notifications',
    'settings_app',
    'files',
    'ai_intake',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.product_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'


DATABASES = {
    'default': env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'

USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")

AZURE_OPENAI = {
    'endpoint': env("AZURE_OPENAI_ENDPOINT"),
    'api_key': env("AZURE_OPENAI_API_KEY"),
    'deployment': env("AZURE_OPENAI_DEPLOYMENT"),
    'api_version': env("AZURE_OPENAI_API_VERSION"),
    'timeout_seconds': env("AZURE_OPENAI_TIMEOUT_SECONDS"),
    'max_retries': env("AZURE_OPENAI_MAX_RETRIES"),
}





