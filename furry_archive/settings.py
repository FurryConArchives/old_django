"""
Django settings for furry_archive project.
"""

from pathlib import Path
import os

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'NSAKFHGvnsdkfnaskfnasDkjasnGsnfkasngksbn3zzsa'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _env_bool('DEBUG', True)

ALLOWED_HOSTS = ["*"]

FRONTEND_HOST = os.environ.get('FRONTEND_HOST', 'old.furryconarchives.org')
API_HOST = os.environ.get('API_HOST', 'api.furryconarchives.org')
FRONTEND_BASE_URL = os.environ.get('FRONTEND_BASE_URL', f'https://{FRONTEND_HOST}').rstrip('/')
API_BASE_URL = os.environ.get('API_BASE_URL', f'https://{API_HOST}').rstrip('/')

CSRF_TRUSTED_ORIGINS = [
    'https://old.furryconarchives.org',
    'https://api.furryconarchives.org',
    'https://furryconarchives.org',
    'https://www.furryconarchives.org',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

USE_B2_STORAGE = _env_bool('USE_B2_STORAGE', False)

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'archive.apps.ArchiveConfig',
    'storages'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'archive.middleware_hosts.HostSplitMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'archive.middleware_sitevisit.UniqueVisitMiddleware',
    'django.middleware.common.CommonMiddleware',
    'archive.middleware_trailing_slash.StripTrailingSlashFallbackMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'furry_archive.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'archive.context_processors.footer_stats',
                'archive.context_processors.git_commit',
                'archive.context_processors.site_banner',
                'archive.context_processors.our_friends',
                'archive.context_processors.site_hosts',
            ],
        },
    },
]

WSGI_APPLICATION = 'furry_archive.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

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


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Media files (user uploaded files)
MEDIA_URL = os.getenv('MEDIA_URL', '/media/')
MEDIA_ROOT = BASE_DIR / 'media'

if USE_B2_STORAGE:
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'archive': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
}

# Login settings
LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/admin/'

# Telegram / Discord bot tokens are only used to fetch profile pictures.
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_PEER = os.environ.get('TELEGRAM_PEER', '').strip().lstrip('@')
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '')

# Internet Archive API
IA_ACCESS_KEY = os.environ.get('IA_ACCESS_KEY', '')
IA_SECRET_KEY = os.environ.get('IA_SECRET_KEY', '')
IA_COLLECTION = os.environ.get('IA_COLLECTION', 'community-texts')

# b2 API
B2_BUCKET_NAME = os.environ.get('B2_BUCKET_NAME', 'furryconarchives')
B2_ENDPOINT_URL = os.environ.get('B2_ENDPOINT_URL', 'https://s3.us-east-005.backblazeb2.com')
B2_REGION_NAME = os.environ.get('B2_REGION_NAME', 'us-east-005')
B2_ACCESS_KEY_ID = os.environ.get('B2_ACCESS_KEY_ID', '')
B2_SECRET_ACCESS_KEY = os.environ.get('B2_SECRET_ACCESS_KEY') or os.environ.get('AWS_SECRET_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = B2_BUCKET_NAME
AWS_S3_ENDPOINT_URL = B2_ENDPOINT_URL
AWS_S3_REGION_NAME = B2_REGION_NAME
AWS_ACCESS_KEY_ID = B2_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = B2_SECRET_ACCESS_KEY
AWS_S3_MAX_POOL_CONNECTIONS = 50
AWS_S3_OBJECT_PARAMETERS = {
    'CacheControl': 'public, max-age=31536000',
}

# Public JSON API rate limits (requests per minute per IP when no app key is provided)
PUBLIC_API_RATE_PER_MIN = int(os.environ.get('PUBLIC_API_RATE_PER_MIN', '1'))

# First-party key used by the legacy website. Sent on every browser API call.
SITE_API_KEY = os.environ.get(
    'SITE_API_KEY',
    '0',
)

# Optional pretalx API token for private instances (public schedules work without it)
PRETALX_API_TOKEN = os.environ.get('PRETALX_API_TOKEN', '')