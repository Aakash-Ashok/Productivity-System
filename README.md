# AI-Powered Employee Productivity and Task Management System

This repository contains a Django web application for user-role management, teams, projects, task tracking, and AI-assisted productivity analytics.

## Features

- Three roles: admin, manager, and employee
- Team management with manager assignment
- Project management linked to teams
- Employee management with skills, experience, availability, and workload capacity
- Task assignment with deadline, difficulty, progress, and priority tracking
- Task log management for recording hours spent and progress updates
- Dashboard with team performance, project progress, workload distribution, burnout alerts, and anomaly signals
- AI-inspired analytics for:
  - predicted completion hours
  - deadline risk classification
  - smart employee recommendation
  - workload balancing and burnout detection
  - stored AI results per task

## Tech Stack

- Django web application
- SQLite by default for easy local setup
- PostgreSQL-ready dependency included via `psycopg2-binary`
- Pandas and Scikit-learn for predictive analytics

## Run Locally

```bash
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

## Demo Login

- Username: `admin`
- Password: `admin123`
- Manager: `manager1 / manager123`
- Employee: `ava / emp123`

## Main Pages

- `/` dashboard
- `/users/` user and role management
- `/teams/` team management
- `/employees/` employee directory
- `/projects/` project management
- `/tasks/` task board
- `/logs/new/` work logging
- `/analytics/` AI analytics view

## Notes

- The AI layer trains simple models from available task and task log data when enough records exist.
- When the dataset is still small, the system falls back to explainable heuristic scoring so the app remains usable during demos and early development.
- New account credentials are emailed through Django's mail backend. By default, emails are printed to the console; set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, and `DEFAULT_FROM_EMAIL` for real SMTP delivery.
- Projects use a reopenable lifecycle: when all project tasks are completed the project becomes `completed`, and if a new task is later added or a task becomes active again, the project automatically returns to `active`.
