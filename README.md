# KnowGap - Course Video Recommendations & Student Risk Prediction

## Overview

**KnowGap** is an intelligent platform designed to enhance student learning and course management. It combines student performance prediction with personalized video recommendations to improve understanding of incorrectly answered quiz questions. The platform integrates with learning management systems like Canvas to monitor student progress and provide instructors with critical insights, while helping students close knowledge gaps through targeted video recommendations.

## Features

1. **Student Risk Prediction**
   - Identifies students at risk of underperforming or failing based on quiz performance and other course metrics.
   - Offers insights to instructors, enabling timely intervention to help students succeed.

2. **Curated Video Recommendations**
   - Automatically generates personalized video recommendations for quiz questions that students answer incorrectly.
   - Leverages predefined or dynamically generated core topics for each question.
   - Stores new video data in the system, ensuring future reuse without redundant lookups.

3. **Integration with Canvas**
   - Uses course and quiz data from Canvas to monitor student performance.
   - Tracks quiz results and triggers video recommendations based on incorrect answers.

4. **Caching and Storing Results**
   - Caches dynamically generated core topics and videos, reducing the need for repeated API calls or queries.

## Tech Stack
 
The backend is a Python application built on the **Quart** framework (an async-capable counterpart to Flask), served in production via **Hypercorn**. Data is stored in **MongoDB**. Configuration is managed through environment variables loaded via `python-dotenv`. The companion frontend is a **React/TypeScript** single-page application. The two services communicate over HTTP, with the frontend calling the backend's REST API.
 
## Local Setup (Without Docker)
 
If you want to run the backend directly on your machine, you may need a Python virtual environment and a `.env` file.
 
Python 3.12 is recommended. Some dependencies in `requirements.txt` (notably `pydantic_core`, which relies on prebuilt wheels) may not have published wheels yet for very new Python releases such as 3.14, which will cause `pip install` to fail while trying to compile from source. If you already have a newer Python version installed, you don't need to remove it. You can just install 3.12 alongside it and point your virtual environment at that version specifically.
 
To set up:
 
```
cd knowgap-backend
py -3.12 -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # macOS/Linux
pip install --upgrade pip
pip install -r requirements.txt
```
 
On Windows, if `venv\Scripts\activate` fails in PowerShell with a "running scripts is disabled" error, this is PowerShell's default execution policy, not an issue with the project. Either run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal session before activating, or activate via Command Prompt instead using `venv\Scripts\activate.bat`.
 
Once the environment is set up, copy `.env.example` to `.env` and fill in real values — a full MongoDB connection string (`DB_CONNECTION_STRING`), an encryption key matching the rest of the team's if you're working against shared data, and any other required variables. Running the backend outside Docker means it will need a MongoDB instance it can actually reach — either a local install or a shared Atlas cluster.
 
## Running with Docker
 
Docker is the recommended way to run the full stack (backend, frontend, and MongoDB together) without needing a local database or worrying about Python version conflicts.
 
**Folder structure**
 
The provided `docker-compose.yml` expects `backend` and `frontend` as folders. The docker-compose.yml file should be inside a Docker folder inside the parent directory of both the frontend and backend folder. Check the paths in the compose file against your actual folder layout before running it, and adjust if you get a "file not found" error pointing at the wrong location.
 
**Backend `.env` for Docker**
 
The MongoDB container does not support TLS, so when running via Docker, the ENVIRONMENT environment variable needs tobe set to development.
 
```
DB_CONNECTION_STRING=mongodb://mongo:27017
ENVIRONMENT=development
```
 
`mongo` here is the container's hostname on Docker's internal network, not `localhost` — it only resolves inside the Docker Compose network. TLS should still be enabled on deployed/production environments, where it connects to a real MongoDB instance instead of the local container.
 
**Starting the stack**
 
From the folder containing `docker-compose.yml`:
 
```
docker compose up --build
```
 
Once running, the backend is reachable at `http://localhost:5001` and the frontend at `http://localhost:3000`. This is also what to point tools like Postman at when testing backend endpoints directly — the containers need to stay running for the API to respond.
 
To stop everything:
 
```
docker compose down
```
 
This stops and removes the containers but preserves MongoDB data in the named `mongo_data` volume, so test data persists between sessions.
 
## Troubleshooting
 
**`pip install` fails to build `pydantic_core`:** This is almost always a Python version mismatch rather than a real dependency problem — see the Local Setup section above for using Python 3.12 in a dedicated virtual environment.
 
**PowerShell blocks `venv\Scripts\activate`:** This is PowerShell's execution policy, unrelated to the project. Use `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for the current session, or activate via Command Prompt instead.
 
**`docker compose up` fails with a "file not found" error for `.env` or a build path:** The compose file's relative paths don't match your actual folder layout. Confirm whether `docker-compose.yml` sits alongside `backend`/`frontend` or in its own subfolder, and adjust `./` vs `../` in the compose file accordingly.
 
**Docker error: "Docker Desktop is manually paused":** Resume Docker Desktop from the whale icon in the system tray, or open the Docker Desktop app directly and look for a resume option.
 
**VS Code notification about "terminal environment injection is disabled":** This only affects whether `.env` values are manually injected into VS Code's integrated terminal for convenience — it has no effect on the app itself, which loads its own `.env` file via `python-dotenv` at startup. Safe to dismiss.

## Backend Directory Structure

The backend is organized into modular subdirectories to keep the codebase maintainable and scalable.

### 1. `routes/`
Defines the API endpoints, organized by feature area: `base_routes.py`, `auth_routes.py`, `user_routes.py`, `course_routes.py`, `video_routes.py`, `canvas_routes.py`, `skill_routes.py`, `progress_routes.py`, `badge_routes.py`, `analytics_routes.py`, `instructor_routes.py`, `support_routes.py`, `achieveup_routes.py`, and `course_utils.py`. Each route file registers its endpoints with the main app instance.

### 2. `services/`
Contains the core business logic and database interactions, separated from the routing layer. Key files include `video_service.py`, `course_service.py`, `user_service.py`, `skill_service.py`, `progress_service.py`, `badge_service.py`, `analytics_service.py`, `mastery_service.py`, `support_service.py`, and several `achieveup_*_service.py` files handling AI, auth, and Canvas-specific logic.

### 3. `utils/`
Shared helper functions: `encryption_utils.py` (token encryption/decryption), `youtube_utils.py` (YouTube API helpers), `ai_utils.py` (AI-generated core topics), `course_utils.py`, and `db_utils.py`.

Current Student View          |  Current Instructor View
:-------------------------:|:-------------------------:
![](https://i.ibb.co/592pv8d/image-2024-10-26-204812751.png)| ![](https://i.ibb.co/hRjdT0R/demo.png)
