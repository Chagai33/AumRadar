# 📡 AumRadar - Automated Music Discovery Engine

![Version](https://img.shields.io/badge/version-2.0-blue.svg)
![Status](https://img.shields.io/badge/status-live-success.svg)
![Spotify](https://img.shields.io/badge/Spotify-1DB954?logo=spotify&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)

> **"From manual execution to zero-touch cloud automation."**

**AumRadar** is a Full-Stack cloud platform built to automate the curation of the "Aum.Music" weekly Spotify playlists.
It represents the evolution of a workflow that ran successfully as a local Python script for 4 years, now upgraded into a fully autonomous SaaS-like application.

**🎵 The Result:** Powering the "Aum.Music Weekly" playlist for **294 consecutive weeks** (and counting).

---

## 🔗 Live Links
* **Web Interface:** [https://aumradar.netlify.app/](https://aumradar.netlify.app/)
* **The Project (Instagram):** [@aum.music](https://www.instagram.com/aum.music/)

---

## 💡 The Evolution (Why I built this)

### Phase 1: The Local Script (2022-2025) 💻
For four years, I maintained a Python script that worked perfectly but had a major bottleneck: **Hardware Dependency.**
To generate the weekly playlist, I had to physically be at my computer, set up the environment, and run the script manually every Friday.

### Phase 2: Cloud Automation (2026 - Present) ☁️
I decided to eliminate the need for a physical computer and upgrade the user experience.
**AumRadar** was born to solve this:
* **Zero Dependency:** The system runs in the cloud (Dockerized). I don't need to be home or near a PC.
* **Mobile Experience:** I can manage artists and view logs directly from my phone via a React Dashboard.
* **Visual Upgrade:** Replaced CLI logs with a clean, modern UI for better tracking.

---

## 🚀 Key Features

* **Autonomous Scheduling:** The system wakes up, scans for new releases, and updates Spotify automatically.
* **Smart Ingestion:** Filters tracks based on user-defined rules and adds them to the weekly playlist.
* **Artist Management UI:** A responsive dashboard to Add/Remove tracked artists without touching the database/code.
* **Real-time Feedback:** Live status updates and logs visible from any device.
* **Secure Auth:** Handles Spotify OAuth2 token refreshing automatically in the background.

---

## 🛠️ Tech Stack

* **Frontend:** React (Vite), TypeScript, Tailwind CSS (Hosted on Netlify).
* **Backend:** FastAPI (Python), AsyncIO (Hosted on Cloud).
* **Data & Caching:** Redis & JSON storage.
* **Containerization:** Docker & Docker Compose.

---

## 📸 Dashboard

*(Add a screenshot of your dashboard here, e.g., `![Dashboard](./dashboard.png)`)*

---

## 👤 Author

**Chagai Yechiel** - *DevOps & Automation Developer*
* **GitHub:** [@Chagai33](https://github.com/Chagai33)
* **LinkedIn:** [Chagai Yechiel](https://www.linkedin.com/in/chagai-yechiel/)
