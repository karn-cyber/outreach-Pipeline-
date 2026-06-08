# Deploying to neelanshukarn.online

This app streams live progress (SSE) and runs background jobs, so it needs a
**persistent server** — Render's free tier is the easiest. (Vercel/Netlify will
NOT work — they're serverless and break the streaming.)

We'll host it at a subdomain: **`outreach.neelanshukarn.online`**
(keeps your root domain + email routing untouched).

---

## Step 1 — Push the code to GitHub  (5 min)

```bash
cd ~/Downloads/outreach-pipeline
git init
git add .
git commit -m "Outreach pipeline"
```
Then create an empty repo on github.com and:
```bash
git remote add origin https://github.com/<you>/outreach-pipeline.git
git branch -M main
git push -u origin main
```
✅ `.env` is gitignored — your API keys are NOT pushed. Good.

---

## Step 2 — Deploy on Render  (5 min)

1. Sign up at **render.com** (free, use GitHub login).
2. **New → Web Service → connect your repo.**
3. Render auto-detects `render.yaml`. Confirm:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn server:app --workers 1 --threads 8 --worker-class gthread --timeout 300 --bind 0.0.0.0:$PORT`
   - Plan: **Free**
4. Click **Create**.

---

## Step 3 — Add your secrets in Render  (the "what to give")

Render dashboard → your service → **Environment** → add these
(everything marked `sync: false` in render.yaml):

| Key | Value |
|-----|-------|
| `OCEAN_API_TOKEN` | your Ocean token |
| `APOLLO_API_KEY` | your Apollo key |
| `PROSPEO_API_KEY` | your Prospeo key |
| `BREVO_API_KEY` | your Brevo REST key |
| `SENDER_EMAIL` | neelanshu@neelanshukarn.online (verified Brevo sender) |
| `REPLY_TO_EMAIL` | neelanshu@neelanshukarn.online |
| `TEST_RECIPIENT` | **your Gmail** (so demo sends land somewhere you check) |
| `APP_PASSWORD` | a password you choose (protects the public URL) |

`USE_PROSPEO_EMAIL`, `SENDER_NAME`, `APP_USERNAME` are already set in render.yaml.

Click **Save** → Render redeploys. Your app is now live at
`https://outreach-pipeline-xxxx.onrender.com`.

---

## Step 4 — Point your domain at it  (5 min, Cloudflare)

1. In Render → your service → **Settings → Custom Domain →** add
   `outreach.neelanshukarn.online`. Render shows a **CNAME target**
   (e.g. `outreach-pipeline-xxxx.onrender.com`).
2. In **Cloudflare → neelanshukarn.online → DNS → Add record:**
   - Type: **CNAME**
   - Name: `outreach`
   - Target: the Render target from step 1
   - Proxy status: **DNS only** (grey cloud) — important for Render's SSL
3. Wait ~2–5 min. Render auto-issues an SSL cert.

✅ Live at **https://outreach.neelanshukarn.online** (login with `admin` / your `APP_PASSWORD`).

---

## Notes

- **Free tier sleeps** after ~15 min idle and cold-starts in ~30s. Open the URL a
  minute before any live demo to warm it up.
- **Suppression list resets** on each redeploy (ephemeral disk) — fine for demos.
- Keep `TEST_RECIPIENT` set so even a curious visitor only ever emails *you*,
  never real prospects.
- To run a real campaign later: blank `TEST_RECIPIENT` and raise the Depth caps.
