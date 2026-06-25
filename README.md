# Asset Platform

Django-based asset management platform with inventory modules, review workflows, and AI-assisted invoice intake.

## Stack

- Django
- Django REST Framework
- Celery
- Redis
- Django templates
- SQLite for local bootstrap, PostgreSQL-ready configuration

## GitHub-Safe Configuration

This repo is set up so local secrets and machine-specific files stay out of source control.

Ignored by default:
- `.env`
- `.env.local`
- `.venv/`
- `db.sqlite3`
- `media/`
- `staticfiles/`

Use `.env.example` as the template for local configuration. Never commit real API keys or production secrets.

## Local Setup

1. Create the virtual environment.
2. Install dependencies.
3. Copy `.env.example` to `.env.local`.
4. Fill in local values only on your machine.
5. Run migrations.
6. Create a superuser.
7. Start the server.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env.local
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## AI Intake Configuration

The AI intake flow works without committed secrets, but Azure OpenAI-backed extraction requires local values for:

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_OPENAI_API_VERSION`

Set them only in your local `.env.local`, never in tracked files.

## Development Notes

Use the project-local interpreter at `product/asset-platform/.venv`.

Examples:

```powershell
.\dev.ps1 check
.\dev.ps1 runserver
.\dev.ps1 test ai_intake
```


