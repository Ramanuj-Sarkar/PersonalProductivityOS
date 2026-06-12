# Productivity

Personal Productivity OS — Daily Planner
Fetches Gmail + Google Calendar directly via Google APIs,
then passes the data to either Claude or a local model for planning.

# How to Run

## Two Possible Setup Steps

The only step which differs between Claude and the local model is the model setup step.

### Claude Setup:
```bash
pip install anthropic google-api-python-client google-auth google-auth-oauthlib
```

Put ANTHROPIC_API_KEY in .env

### Local Model Setup

You have to install the local model first.

1. Install Ollama from https://ollama.com/download
2. Pull a model:
  * ```ollama pull llama3.2```      # fast, ~2GB
  * ```ollama pull llama3.1:8b```   # smarter, ~5GB (recommended)
  * ```ollama pull mistral```       # good alternative, ~4GB
3. Start server: ```ollama serve```

```bash
pip install ollama google-api-python-client google-auth google-auth-oauthlib
```

## First-time Google OAuth setup
1. Go to https://console.cloud.google.com
2. Create a project → Enable "Gmail API" and "Google Calendar API"
3. APIs & Services → Credentials → Create OAuth 2.0 Client ID (Desktop App)
4. Download and save as client_secret.json in this directory
5. APIs & Services → OAuth consent screen → Test users → add your Gmail address
6. Run the script — a browser window opens once to authorize, then caches token.json

## Usage:
```
python daily_planner.py
```

