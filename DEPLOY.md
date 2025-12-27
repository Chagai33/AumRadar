# Deployment Guide for Antigravity

This guide explains how to deploy your app to Google Cloud Run (Backend) and Netlify (Frontend) for free.

## Prerequisites
1. A GitHub account.
2. A Google Cloud Platform (GCP) account with billing enabled (for identity verification).
3. A Netlify account.

## Step 1: Push Code to GitHub
1. Create a new repository on GitHub.
2. Push all your code to this repository.

## Step 2: Google Cloud Setup (Backend)

### 1. Create a Storage Bucket
This is where your scan results will be saved.
1. Go to **Google Cloud Console** -> **Cloud Storage** -> **Buckets**.
2. Click **Create**.
3. Name it (e.g., `antigravity-storage`).
4. Choose **Region** (use `us-central1` or `europe-west1` for free tier compatibility).
5. Click **Create**.

### 2. Deploy to Cloud Run
1. Go to **Google Cloud Run**.
2. Click **Create Service**.
3. Select **Continuously deploy new revisions from a source repository**.
4. Click **Set up with Cloud Build**.
5. Connect your GitHub repo.
6. **Build Configuration**:
   - **Build Type**: Dockerfile.
   - **Source location**: `/` (root).
7. **Service Settings**:
   - **Service Name**: `antigravity-backend`.
   - **Region**: Same as your bucket.
   - **Authentication**: Allow unauthenticated invocations (Check this box).
   - **CPU allocation**: CPU is only allocated during request processing.
8. **Environment Variables**:
   Expand **Container, Networking, Security** -> **Variables & Secrets**.
   Add the following variables:
   - `CLIENT_ID`: Your Spotify Client ID.
   - `CLIENT_SECRET`: Your Spotify Client Secret.
   - `BUCKET_NAME`: The name of the bucket you created (e.g. `antigravity-storage`).
   - `allowed_origins`: `https://YOUR-NETLIFY-SITE-NAME.netlify.app` (You will update this later).
   - `FRONTEND_URL`: `https://YOUR-NETLIFY-SITE-NAME.netlify.app` (You will update this later).
   - `REDIRECT_URI`: `https://YOUR-CLOUD-RUN-URL.run.app/callback` (You'll get this URL after deployment).
   - `SECRET_KEY`: A random secret password.

9. Click **Create**.

### 3. Update Spotify Dashboard
Once deployed, copy the **Service URL** (e.g., `https://antigravity-backend-xyz.a.run.app`).
1. Go to Spotify Developer Dashboard.
2. Edit Settings.
3. Add Redirect URI: `https://YOUR-CLOUD-RUN-URL.run.app/callback`.
4. Save.

## Step 3: Netlify Setup (Frontend)

1. Go to [Netlify](https://app.netlify.com/).
2. Click **Add new site** -> **Import from an existing project**.
3. Connect GitHub.
4. Select your repo.
5. **Build Settings**:
   - **Base directory**: `frontend`
   - **Build command**: `npm run build`
   - **Publish directory**: `dist`
6. **Environment Variables**:
   Click **Show advanced**.
   - `VITE_API_URL`: The URL of your Google Cloud Run service (e.g., `https://antigravity-backend-xyz.a.run.app`).
7. Click **Deploy**.

## Step 4: Final Glue
1. Copy your new Netlify URL (e.g., `https://coool-site-123.netlify.app`).
2. Go back to **Google Cloud Run** -> Edit Service.
3. Update `ALLOWED_ORIGINS` and `FRONTEND_URL` with this Netlify URL.
4. Redeploy.

Done! 🚀
