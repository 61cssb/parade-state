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
Railway Postgres (private network only — never publicly exposed)
  └─ nightly GitHub Actions job ("Database backup")
       ├─ ssh <grant-uuid>@ssh.railway.com → app container
       │    └─ pg_dump (custom format), streamed back over the SSH channel
       ├─ age-encrypt with the super-admin's PUBLIC key
       └─ rclone upload → Google Drive folder owned by the super-admin
            └─ 30-day retention sweep
```

> **History:** the original setup (2026-08-19) reached Postgres through a
> Railway *public TCP proxy* URL. Railway retired public TCP proxies in
> 2026-10 (the API no longer allows creating them, and existing proxies
> were removed platform-side — the 2026-10-22..25 backup outage), so the
> pipeline now execs `pg_dump` inside the app container over Railway SSH.
> Per house standard, the database is never given a public endpoint again.

Decisions and rationale: see [DEPLOYMENT.md](DEPLOYMENT.md) → *Backup
Strategy*. Restore procedure: [DEPLOYMENT.md](DEPLOYMENT.md) → *Restore
Procedure*.

## Prerequisites

- Admin access to: the GitHub repo, the Railway project, a Google account
  (the super-admin's), and any machine with `age` installed
- The backup workflow merged to `main`
  (`.github/workflows/backup-db.yml`)

---

## Step 1 — Railway: register the CI SSH key

The database is reachable only on Railway's private network. The backup job
reaches it by SSH-ing into the **app** container (which has `DATABASE_URL`
and ships PostgreSQL client 18) and running `pg_dump` there. Auth is an SSH
key registered with Railway — no database credential ever reaches GitHub.

1. Generate a dedicated keypair (do **not** reuse a personal key):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/railway_ci_backup -N "" \
     -C "github-ci-backup@parade-state"
   ```

2. Register the public half as a **workspace-owned** key (the right tier
   for shared/CI credentials — revocable without touching personal keys):

   ```bash
   railway ssh keys add --workspace -k ~/.ssh/railway_ci_backup.pub \
     -n github-ci-backup-parade-state
   ```

3. Get the SSH *grant UUID* for the app service in the **production**
   environment. The UUID is environment-pinned — generate it against the
   exact environment you mean to hit (dev has a different UUID):

   ```bash
   railway ssh config --dry-run --path /dev/null \
     -p 92d80df3-6565-4ca3-8a00-1bb44ef2898b \
     -s 1ea09699-73a7-4ad3-abdf-799a53a9afa5 \
     -e e6250e41-8967-43da-a1b7-ba5d51b4bc8b --alias tmp
   # → User <grant-uuid>   (the workflow's SSH_USER value)
   ```

4. Put the **private** half in the repo secret `RAILWAY_SSH_KEY`:

   ```bash
   gh secret set RAILWAY_SSH_KEY < ~/.ssh/railway_ci_backup
   ```

5. Sanity check — this should stream a valid dump from production:

   ```bash
   ssh -i ~/.ssh/railway_ci_backup \
     <grant-uuid>@ssh.railway.com -- \
     'pg_dump "$DATABASE_URL" --format=custom --no-owner --no-privileges' \
     > /tmp/check.dump
   pg_restore --list /tmp/check.dump | head   # TOC prints = dump is sound
   rm /tmp/check.dump
   ```

> ⚠️ **Pitfall (by design):** the grant UUID is *not* a secret — it
> authenticates nothing without a registered key — but it IS
> environment-specific. Double-check you took the production UUID; a
> backup of the dev database would succeed silently while backing up the
> wrong data.

## Step 2 — GitHub Actions: secrets and variable

Settings → Secrets and variables → Actions.

| Name | Kind | Value |
|---|---|---|
| `RAILWAY_SSH_KEY` | **Secret** | private half of the CI keypair from Step 1 |
| `AGE_PUBLIC_KEY` | **Secret** | `age1...` public key from Step 3 |
| `GDRIVE_OAUTH_TOKEN` | **Secret** | token JSON from Step 4 |
| `GDRIVE_ROOT_FOLDER_ID` | **Variable** ⚠️ | folder ID from Step 5 |

> The old `RAILWAY_PUBLIC_DATABASE_URL` secret is obsolete (Railway
> retired public TCP proxies) — delete it if it still exists.

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

## Step 7 — Restoring a backup

**Primary path — the admin UI (no shell access needed):**

1. On any machine, decrypt the backup offline:
   ```bash
   age -d -i parade-state-backup.key -o backup.dump parade-state-*.dump.age
   ```
2. Sign in to the app as the super-admin → **DB Restore**
   (`/admin/database-restore`) → upload `backup.dump` → type the
   database name to confirm → *Restore database*.
3. The app verifies the dump in a scratch database (schema version,
   core tables) and only swaps it into place after it passes; the page
   shows a verification summary (schema revision, table/row counts)
   when done. Failed verification leaves the live database untouched.

The restore requires the running deployment to have `pg_restore`
available (the Docker image ships PostgreSQL client 18) and can be
disabled with `RESTORE_ENABLED=false`.

**Fallback path — CLI** (when the app itself is down, or restore into a
fresh Railway database):

```bash
# download the .dump.age from Drive, then:
age -d -i parade-state-backup.key -o backup.dump parade-state-*.dump.age
pg_restore --list backup.dump | head   # sanity: the TOC = dump is sound
pg_restore --no-owner --no-privileges --dbname="$RESTORE_DATABASE_URL" backup.dump
```

Full CLI procedure with local-verification steps:
[DEPLOYMENT.md](DEPLOYMENT.md) → *Restore Procedure*.

---

## Troubleshooting (all observed or anticipated)

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied (publickey)` in the readiness or dump step | The key in `RAILWAY_SSH_KEY` is no longer registered (`railway ssh keys list`), was removed, or the secret holds the wrong half | Re-register or regenerate per Step 1; re-set the secret with the private half |
| `HOST KEY VERIFICATION FAILED` | ssh.railway.com rotated its host key (rare; verify out-of-band first — a mismatch can also mean interception) | Confirm the new key via `ssh-keyscan -t ed25519 ssh.railway.com` against Railway's published fingerprint, then update the pinned line in the workflow |
| `Permission denied` with a valid key + `User ... not found` style error | The grant UUID changed (Railway rotates grants on service/env changes) | Re-run the Step 1 `railway ssh config --dry-run` and update `SSH_USER` in the workflow |
| `pg_dump: command not found` (remote) | The app image no longer ships PostgreSQL client 18 | Restore the client in the Docker image (keep it same-major-or-newer than the server) |
| `pg_dump: server version mismatch` (remote) | Server major newer than the client in the app image | Bump the PostgreSQL client in the app image — pg_dump runs remotely now, so this is an app-image fix, not a workflow fix |
| Connection works locally, fails in Actions | (pre-2026-10 proxy era: missing `sslmode`) Obsolete — the dump no longer leaves Railway; treat any new failure as an SSH/auth issue above | — |
| Job env shows `ROOT_FOLDER:` empty; upload misbehaves | Folder ID filed as a **secret**, not variable | Delete secret; re-add under the Variables tab |
| Upload: `403 ... storageQuotaExceeded ... Service Accounts do not have storage quota` | Auth set up as a service account — Google no longer allows SAs to own My Drive files on consumer accounts | Use the user-OAuth token from Step 4 (`GDRIVE_OAUTH_TOKEN`) |
| Upload: `401` / `invalid_grant` | The OAuth refresh token was revoked (app removed in Google Account settings, Google password change, or six months unused) | Re-run `rclone authorize "drive"`, update `GDRIVE_OAUTH_TOKEN` |
| Upload: `invalid character 't' looking for beginning of object key string` | A JSON secret corrupted in transit — historically by interpolating the secret directly into a shell script (its double quotes broke quoting); the workflow now passes secrets via `env:` and validates before use | If it recurs, the pasted secret itself is bad — re-copy the full JSON from `rclone authorize` |
| `FATAL: the database system is starting up` | Postgres cold-starting (serverless) or restarting | The workflow now polls up to 2 min; if it persists, the DB is crash-looping — check Railway |
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
- **Rotating the Drive OAuth token:** the workflow authenticates with the
  `GDRIVE_OAUTH_TOKEN` secret only (user OAuth from Step 4 — no service
  account is involved). If uploads start failing with `401` /
  `invalid_grant` (revoked app, Google password change, or six months
  unused), re-run `rclone authorize "drive"` and update the secret.

**See also:** [DEPLOYMENT.md](DEPLOYMENT.md) for the backup decision
record, restore procedure, and disaster recovery plan.
