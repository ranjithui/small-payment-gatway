# Deploying FeeFlow (HTTPS) so `app-release.apk` works

`app-release.apk` only talks to an **https://** server. This guide puts the backend on
[Render](https://render.com), which provides HTTPS automatically. The [`render.yaml`](../render.yaml)
blueprint creates the web service and a PostgreSQL database in one go.

Verified before deployment:
* Full backend test suite on PostgreSQL 16: 164 tests, plus real-concurrency tests (10 simultaneous taps → one
  declaration; 5 simultaneous admin confirms → one credit).
* A local production rehearsal using the same start command as the container. It covered
  refusal of unsafe settings, first-start bootstrap, HTTPS links, and HSTS.

The Docker image itself (Python 3.11) was not built locally, because Docker isn't installed here.
Render builds it on deploy.

## 1. Put the code on GitHub (you)

Create an **empty private** repository on GitHub, e.g. `feeflow`. Then, from the project folder:

```bash
git init -b main
```

```bash
git add . && git status
```

Check that `git status` does **not** list `android/keystore/`, `android/keystore.properties`,
`feeflow.db`, `.env` or `seed_output.txt`; they are git-ignored. Then:

```bash
git commit -m "FeeFlow v2.1"
```

```bash
git remote add origin https://github.com/<you>/feeflow.git && git push -u origin main
```

## 2. Create the Render services (you)

1. Sign up at render.com with your GitHub account.
2. **New → Blueprint**, then pick the `feeflow` repository. Render reads `render.yaml`.
3. Fill in the values it asks for. They are used once, to create your centre and first admin:

   | Key | Example |
   |---|---|
   | `FEEFLOW_BOOTSTRAP_CENTRE_NAME` | Bright Minds Tuition Centre |
   | `FEEFLOW_BOOTSTRAP_CENTRE_CODE` | BMTC (used in receipt numbers) |
   | `FEEFLOW_BOOTSTRAP_UPI_VPA` | your centre's real UPI ID, e.g. `brightminds@okaxis` |
   | `FEEFLOW_BOOTSTRAP_UPI_NAME` | name shown in UPI apps |
   | `FEEFLOW_BOOTSTRAP_CONTACT_PHONE` | +91 98765 43210 |
   | `FEEFLOW_BOOTSTRAP_ADMIN_EMAIL` | your email |
   | `FEEFLOW_BOOTSTRAP_ADMIN_PASSWORD` | a strong password, 10+ characters |

   `FEEFLOW_SECRET_KEY` is generated automatically. `DATABASE_URL` is wired to the new database.
4. Click **Apply**. The first build takes a few minutes.
5. Open `https://<service>.onrender.com/healthz`. It should show `{"status":"ok"}`.
   Then sign in at `https://<service>.onrender.com/admin`.
6. After the first sign-in you can delete `FEEFLOW_BOOTSTRAP_ADMIN_PASSWORD` in Render → Environment.
   It is only read when the database is empty.

**Plans:** the blueprint uses free plans for trying it out. Free web services sleep after 15 minutes
idle (the next request takes about a minute), and free databases are deleted after 30 days. For real
centres switch to **Starter** (web) and **Basic** (database) in `render.yaml` or the dashboard.

**Using Supabase instead of Render's database:** remove the `databases:` block and set `DATABASE_URL`
to the Supabase connection string (*Project Settings → Database → Connection string → URI*). Tables
are created automatically on first start. If you are merging into an existing FeeFlow schema, apply
`migrations/001_feeflow_payment_workflow.sql` instead. It has been tested on PostgreSQL 16 and is
safe to re-run.

## 3. Point the app at it

If Render gave the service exactly `https://feeflow-api.onrender.com`, `app-release.apk` already has
it pre-filled. Otherwise rebuild with your URL:

```bash
cd android && JAVA_HOME=E:/android-toolchain/jdk17 ./gradlew assembleRelease -PreleaseServerUrl=https://<service>.onrender.com
```

Or just type the URL on the app's sign-in screen; any `https://` address works.

## 4. On the phone

1. Uninstall any earlier FeeFlow build signed with a different key (the debug build).
2. Install `android/app/build/outputs/apk/release/app-release.apk`. If Play Protect warns, tap
   **More details → Install anyway**.
3. Sign in with the bootstrap admin email and password.
4. **SMS phone** tab → **Use this phone as the SMS device** → allow SMS.
5. In the web admin: **Fees → + Student**, then **+ Fee**. The parent gets the payment link by SMS
   from the phone.

## Custom domain (optional)

Render → Settings → Custom Domains, e.g. `pay.yourcentre.in`. Then set
`FEEFLOW_PUBLIC_BASE_URL=https://pay.yourcentre.in` so SMS links and receipts use it, and rebuild
the app with `-PreleaseServerUrl=https://pay.yourcentre.in`.
