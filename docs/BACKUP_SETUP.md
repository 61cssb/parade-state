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
| `GDRIVE_SERVICE_ACCOUNT_JSON` | **Secret** | entire JSON file from Step 4 |
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

## Step 4 — Google Cloud: service account

1. console.cloud.google.com → create/reuse a project (e.g.
   `parade-state-backups`)
2. APIs & Services → Library → **Google Drive API** → Enable
   (⚠️ forgetting this, or enabling it on a different project than the
   service account, is the usual cause of 403s on upload)
3. IAM & Admin → Service Accounts → Create (`backup-writer`) → open it →
   Keys → Add key → **JSON** → download
4. Entire file contents → secret `GDRIVE_SERVICE_ACCOUNT_JSON`
5. Copy the `client_email` from the JSON — needed in Step 5

## Step 5 — Google Drive: the backup folder

The folder must be created by **the super-admin in their own Drive** —
human-owned, human-visible, on the human's storage quota. (A folder
created *by the service account* would live in the SA's invisible
storage and orphan if the SA is ever deleted — do not invert this.)

1. drive.google.com (signed in as the super-admin) → New folder, e.g.
   `parade-state backups`
2. Share → paste the `client_email` from Step 4 → role **Editor**.
   Google warns the address is external — that is normal for service
   accounts.
3. Open the folder; copy the ID from the URL:
   `https://drive.google.com/drive/folders/<FOLDER_ID>`
4. `<FOLDER_ID>` → repository **variable** `GDRIVE_ROOT_FOLDER_ID`

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
| Upload: `403` / `directory not found` | Drive API not enabled on the SA's project, folder shared with a typo'd `client_email`, or wrong folder ID | Verify Step 4 item 2 and Step 5 |
| Upload: `invalid character 't' looking for beginning of object key string` | The service-account file corrupted in transit — historically by interpolating the secret directly into a shell script (its double quotes broke quoting); the workflow now passes secrets via `env:` and validates the JSON before use | If it recurs, the pasted secret itself is bad — re-validate the downloaded JSON per Step 4 |
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
