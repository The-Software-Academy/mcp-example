# Gmail API Credentials Setup

This directory holds OAuth2 credentials for the Gmail API. **Never commit `credentials.json` or `token.json` to git** — they are excluded in `.gitignore`.

## Step-by-step setup

### 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com>
2. Create a new project (or select an existing one)
3. Navigate to **APIs & Services → Library**
4. Search for **Gmail API** and click **Enable**

### 2. Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External** (or Internal for Google Workspace)
3. Fill in the required fields: app name, support email, developer email
4. On the **Test users** page, add your Google account email
5. Save and continue through the remaining steps

### 3. Create OAuth 2.0 credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Choose **Desktop app** as the application type
4. Click **Create** then **Download JSON**
5. Google downloads a file named something like `client_secret_<id>.apps.googleusercontent.com.json`
6. **Rename it** and save as `credentials/credentials.json` in this project

### 4. Configure environment variables

Copy the example file and fill in the paths:

```bash
cp .env.example .env
```

The relevant variables:

```bash
GMAIL_CREDENTIALS_PATH=./credentials/credentials.json
GMAIL_TOKEN_PERSISTENCY_PATH=./credentials/token.json
```

### 5. First run — OAuth2 consent flow

Start the server, connect your MCP client, then call the **`gmail_authenticate`** tool.
The tool returns a URL — open it in your browser and grant access.
Google redirects to `http://localhost:8001/oauth/callback`, which hot-reloads Gmail
into the running server without a restart.

```bash
uv run mcp-server
# then in your MCP client: call gmail_authenticate
```

After you grant access, `credentials/token.json` is written.
Subsequent server starts auto-refresh the token silently — no consent needed again.

### 6. Required OAuth scopes

The server requests these scopes:

| Scope | Purpose |
|---|---|
| `gmail.readonly` | Read messages, threads, labels |
| `gmail.send` | Send and reply to messages |
| `gmail.modify` | Mark messages as read/unread |

---

## Troubleshooting

**"Access blocked: This app's request is invalid"**
Add your Google account as a **Test user** in the OAuth consent screen (step 2 above).

**"redirect_uri_mismatch"**
Make sure you chose **Desktop app** (not Web application) as the credential type.

**Token expired / refresh error**
Delete `credentials/token.json` and restart the server to trigger a fresh consent flow.

**Wrong Google account signed in**
Delete `credentials/token.json` and restart — you'll be prompted to sign in again.

**Zone.Identifier files on Windows/WSL**
When downloading on Windows, a `filename:Zone.Identifier` sidecar file may appear.
It is harmless and ignored by Git — you can delete it or leave it.
