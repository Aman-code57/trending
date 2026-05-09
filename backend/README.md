# Backend (FastAPI)

## 1) Create environment

Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

## 2) Configure environment

Copy `.env.example` → `.env` and fill values.

## 3) Run API

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

API base: `http://localhost:8000/api/v1`

Health: `http://localhost:8000/health`

## Instagram data notes (important)

To fetch real Instagram data legally/reliably you typically use the **Instagram Graph API** (Meta).
You need:

- A Meta App
- An Instagram Professional account (Business/Creator) connected to a Facebook Page
- Permissions/features approved (depending on endpoints), and an Access Token
- Your Instagram User ID (IG User) used by the Graph API

This repo ships with a **mock provider** so the frontend works immediately.
When you add credentials in `.env`, the API will switch to the Graph provider where implemented.

