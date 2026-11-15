import os
from pathlib import Path

from corsheaders.defaults import default_headers
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'change-me-in-production')
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'corsheaders',
    'rest_framework',
    'sfb_api.api',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'sfb_api.urls'
WSGI_APPLICATION = 'sfb_api.wsgi.application'

# No local database — everything goes through Supabase
DATABASES = {}

REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'sfb_api.api.permissions.SfbApiKeyPermission',
    ],
}

# CORS — allow Lovable frontend
CORS_ALLOWED_ORIGINS = [
    'https://sfb-demo.lovable.app',
    'https://bright-web-workshop-37.lovable.app',
    'http://localhost:5173',
    'http://localhost:3000',
]
CORS_ALLOW_HEADERS = (*default_headers, 'x-sfb-key')
CORS_ALLOW_ALL_ORIGINS = os.environ.get('CORS_ALLOW_ALL', 'False').lower() == 'true'

# Supabase
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')  # service_role key

# OpenAI
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')

# Shared secret between Lovable and this API. Unset means the check is off.
# GET /api/vehicles/ stays public either way. Health stays public.
SFB_API_SECRET = os.environ.get('SFB_API_SECRET', '')

# When true, fitment and watch require a paid Supabase user (Bearer token).
PAID_GATE = os.environ.get('PAID_GATE', 'False').lower() == 'true'

# Stripe subscription. Checkout and the webhook stay disabled until these are set.
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')
STRIPE_SUCCESS_URL = os.environ.get(
    'STRIPE_SUCCESS_URL',
    'https://bright-web-workshop-37.lovable.app/account?checkout=success',
)
STRIPE_CANCEL_URL = os.environ.get(
    'STRIPE_CANCEL_URL',
    'https://bright-web-workshop-37.lovable.app/pricing?checkout=cancel',
)

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
