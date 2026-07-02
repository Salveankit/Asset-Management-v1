# Asset Platform

Django-based asset management platform with inventory modules, review workflows, and AI-assisted invoice intake.

## Stack

- Django
- Django REST Framework
- Celery
- Redis
- Django templates
- PostgreSQL-ready configuration for production, SQLite only for local development

## GitHub-Safe Configuration

This repo is set up so local secrets and machine-specific files stay out of source control.

Ignored by default:
- `.env`
- `.env.local`
- `.venv/`
- `db.sqlite3`
- `media/`
- `staticfiles/`

Use `.env.example` as the template for local configuration. For production on Vercel, create a separate env file or use Vercel environment variables with `DATABASE_URL` pointing to Neon PostgreSQL. Never commit real API keys or production secrets.

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

The AI intake flow works without committed secrets. `AI_INTAKE_PROVIDER=auto` selects Gemini when
`GEMINI_API_KEY` is present, otherwise it selects Azure OpenAI when all Azure settings are present.

For Gemini image and PDF extraction, set:

- `AI_INTAKE_PROVIDER=gemini`
- `GEMINI_API_KEY`
- `GEMINI_MODEL=gemini-2.5-flash`

For Vercel, add `GEMINI_API_KEY` as a Project Environment Variable for Production and Preview, then
redeploy. `GEMINI_MODEL` and `GEMINI_TIMEOUT_SECONDS` have deployment-safe defaults, so they are optional.
Keep API keys out of `.env.example` and all committed files.

For Azure OpenAI-backed extraction, set `AI_INTAKE_PROVIDER=azure_openai` and provide:

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_OPENAI_API_VERSION`

Set them only in your local `.env.local` for development, or in Vercel environment variables for production, never in tracked files.

## Development Notes

Use the project-local interpreter at `product/asset-platform/.venv`.

Examples:

```powershell
.\dev.ps1 check
.\dev.ps1 runserver
.\dev.ps1 test ai_intake
```

## Production Storage Note

On Vercel, uploaded documents are stored in the runtime temp filesystem at /tmp/media. That is enough for the current upload and review flow, but it is not durable storage across redeploys or cold starts. If you need permanent document retention, connect a real object store later.


