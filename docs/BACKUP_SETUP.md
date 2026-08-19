# Backup First-Time Setup Runbook (Super-Admin)

Step-by-step guide for standing up the automated database backup pipeline.
Every pitfall below was hit or anticipated during the original setup
(2026-08-19) — follow the steps in order and check the troubleshooting
table when something fails.

**Audience:** the designated super-admin (or whoever does the deployment).
The current super-admin is whoever signs in with the `SUPER_ADMIN_EMAIL`
configured on the Railway app service; that address bootstraps the first
`super_admin` account on an empty database, and any super-admin can
promote more via `/admin/users`.

## What you are building

```
Railway Postgres (PG18, public TCP proxy)
  └─ nightly GitHub Actions job ("Database backup")
       ├─ pg_dump (custom format)
       ├─ age-encrypt with the super-admin's PUBLIC key
       └─ rclone upload → Google Drive folder owned by the super-admin
            └─ 30-day retention sweep
```

Decisions and rationale: see [DEPLOYMENT.md](DEPLOYMENT.md) → *Backup
Strategy*. Restore procedure: [DEPLOYMENT.md](DEPLOYMENT.md) → *Restore
Procedure*.

## Prerequisites

- Admin access to: the GitHub repo, the Railway project, a Google account
  (the super-admin's), and any machine with `age` installed
- The backup workflow merged to `main`
  (`.github/workflows/backup-db.yml`)

---

## Step 1 — Railway: enable the public TCP proxy

The Postgres service's internal URL is only reachable inside Railway.

1. Railway dashboard → project `parade-state` → service **Postgres** →
   Settings → Networking → **Public TCP Proxy** → enable
2. A `DATABASE_PUBLIC_URL` variable appears on the service (also visible
   via `railway variables --service Postgres`). It looks like
   `postgresql://postgres:<password>@<host>:<port>/railway`

**Append `?sslmode=require`** — Railway omits it, and backups should
never cross the internet unencrypted. The final value:

```
postgresql://postgres:<password>@<host>:<port>/railway?sslmode=require
```

> ⚠️ **Pitfall (hit during setup):** the secret must be this *complete
> URL*. Pasting only `host:port` makes `pg_dump` fail immediately.

Sanity check from any machine with `psql`:

```bash
psql "<full URL>" -tAc "SELECT version();"
```

## Step 2 — GitHub Actions: secrets and variable

Settings → Secrets and variables → Actions.

| Name | Kind | Value |
|---|---|---|
| `RAILWAY_PUBLIC_DATABASE_URL` | **Secret** | full URL from Step 1 |
| `AGE_PUBLIC_KEY` | **Secret** | `age1...` public key from Step 3 |
| `GDRIVE_OAUTH_TOKEN` | **Secret** | token JSON from Step 4 |
| `GDRIVE_ROOT_FOLDER_ID` | **Variable** ⚠️ | folder ID from Step 5 |

> ⚠️ **Pitfall (hit during setup):** `GDRIVE_ROOT_FOLDER_ID` must be a
> repository **variable** (Variables tab), not a secret. The workflow
> reads `vars.` — if misfiled as a secret it silently resolves to empty
> and the job env shows `ROOT_FOLDER:` blank.

## Step 3 — Encryption keypair (age)

On any machine (`brew install age` / `pacman -S age` / `apt install age`):

```bash
age-keygen -o parade-state-backup.key
# → prints: # public key: age1...
```

- The `age1...` string → secret `AGE_PUBLIC_KEY`
- `parade-state-backup.key` → the super-admin's password manager, and
  **nowhere else**. GitHub never sees it. Losing it means every existing
  backup is permanently unreadable — this is the single most important
  artifact of the whole setup.

Rotating later: generate a new keypair, update the secret; keep the old
private key until all backups made with it have aged out of retention.

## Step 4 — Google Drive: authorize rclone as the super-admin

> **Why user OAuth and not a service account:** Google no longer allows
> service accounts to own files in a personal My Drive — uploads fail
> with `403 storageQuotaExceeded` telling you to use shared drives or
> OAuth delegation. Shared Drives need a paid Workspace account, so for
> a consumer Gmail the supported path is the super-admin authorizing
> rclone with their own account. Backups are then owned by that account
> and count against its storage quota.

On the super-admin's own machine (needs a browser):

```bash
# install rclone first: brew install rclone / pacman -S rclone /
# apt install rclone / https://rclone.org/downloads
rclone authorize "drive"
```

A browser opens → sign in as the super-admin → approve access. The
command prints a **token JSON** (one line starting `{"access_token":...`).
Copy the entire JSON — including the braces — and paste it as the secret
**`GDRIVE_OAUTH_TOKEN`**.

Notes:

- The token contains a long-lived refresh token; rclone renews access
  tokens automatically. It dies if the super-admin revokes the app in
  their Google Account settings or (sometimes) changes their Google
  password — if the nightly job suddenly 401s, re-run
  `rclone authorize "drive"` and update the secret.
- rclone's shared OAuth client is used by default; for a
  higher-volume deployment, register your own OAuth client and pass its
  ID/secret to rclone.

## Step 5 — Google Drive: the backup folder

Created by **the super-admin in their own Drive** — human-owned,
human-visible, on the human's storage quota. With user-OAuth auth no
sharing step is needed; the token already acts as the super-admin.

1. drive.google.com (signed in as the super-admin) → New folder, e.g.
   `parade-state backups`
2. Open the folder; copy the ID from the URL:
   `https://drive.google.com/drive/folders/<FOLDER_ID>`
3. `<FOLDER_ID>` → repository **variable** `GDRIVE_ROOT_FOLDER_ID`

## Step 6 — First run and verification

GitHub → Actions → **Database backup** → *Run workflow* → `main`.

Expected: all steps green within a few minutes, and a
`parade-state-<timestamp>.dump.age` file in the Drive folder.

If it fails, open the failed step's log (or
`gh run view <run-id> --log-failed`) and match it against the table
below.

## Step 7 — One-time restore sanity check

An untested backup is a hope, not a backup. Once (any machine with
`age` + `pg_restore` ≥ 18):

```bash
# download the .dump.age from Drive
age -d -i parade-state-backup.key -o backup.dump parade-state-*.dump.age
pg_restore --list backup.dump | head   # prints the TOC = dump is sound
```

The full tested restore procedure lives in
[DEPLOYMENT.md](DEPLOYMENT.md) → *Restore Procedure*.

---

## Troubleshooting (all observed or anticipated)

| Symptom | Cause | Fix |
|---|---|---|
| `pg_dump: connection ... failed` immediately | Secret holds only `host:port` | Replace with the full `postgresql://` URL |
| Connection works locally, fails in Actions | `sslmode` missing/typo'd in the URL | Append `?sslmode=require` |
| Job env shows `ROOT_FOLDER:` empty; upload misbehaves | Folder ID filed as a **secret**, not variable | Delete secret; re-add under the Variables tab |
| Upload: `403 ... storageQuotaExceeded ... Service Accounts do not have storage quota` | Auth set up as a service account — Google no longer allows SAs to own My Drive files on consumer accounts | Use the user-OAuth token from Step 4 (`GDRIVE_OAUTH_TOKEN`) |
| Upload: `401` / `invalid_grant` | The OAuth refresh token was revoked (app removed in Google Account settings, Google password change, or six months unused) | Re-run `rclone authorize "drive"`, update `GDRIVE_OAUTH_TOKEN` |
| Upload: `invalid character 't' looking for beginning of object key string` | A JSON secret corrupted in transit — historically by interpolating the secret directly into a shell script (its double quotes broke quoting); the workflow now passes secrets via `env:` and validates before use | If it recurs, the pasted secret itself is bad — re-copy the full JSON from `rclone authorize` |
| `FATAL: the database system is starting up` | Postgres cold-starting (serverless) or restarting | The workflow now polls up to 2 min; if it persists, the DB is crash-looping — check Railway |
| `pg_dump: server version mismatch` | Server major newer than the client (server is PostgreSQL 18; the runner's preinstalled client 16 shadows the installed 18 unless `/usr/lib/postgresql/18/bin` is first on `PATH`) | Workflow already handles this; if Railway upgrades the server major again, bump `postgresql-client-NN` **and** the PATH line in the workflow |
| `rclone: command not found` | Runner image no longer preinstalls rclone | Workflow already installs it |
| No scheduled runs happening | GitHub disables schedules after **60 days without repo activity** | Push any commit or run a manual dispatch — especially before the annual intensive-use window |

## Ongoing operations

- **Schedule:** daily 19:23 UTC (03:23 SGT) + manual dispatch anytime;
  retention 30 days, enforced by the same job.
- **Failure alerts:** GitHub emails the workflow-triggering user on
  failure — check the Actions tab if backups seem to have stopped.
- **Railway serverless:** keeping the DB always-on avoids cold starts
  but continuously drains the free tier's usage credit. With the
  readiness poll in place, serverless can be re-enabled safely.
- **Rotating the service-account key:** new JSON → update the
  `GDRIVE_SERVICE_ACCOUNT_JSON` secret. Old key keeps working until
  deleted in the console.

**See also:** [DEPLOYMENT.md](DEPLOYMENT.md) for the backup decision
record, restore procedure, and disaster recovery plan.
