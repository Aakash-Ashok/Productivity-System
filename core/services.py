from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression

from .models import AIResult, Activity, Employee, Project, Request, Task, TaskLog, Team


@dataclass
class AllocationSuggestion:
    employee: Employee
    score: float
    reasons: list[str]


@dataclass
class ProjectAIInsight:
    project: Project
    health_score: int
    delay_risk: float
    completion_confidence: int
    recommended_action: str


def send_credentials_email(email: str, username: str, password: str, role: str) -> None:
    if not email:
        return
    subject = 'Your Productivity System account'
    message = (
        f'Your account has been created.\n\n'
        f'Role: {role}\n'
        f'Username: {username}\n'
        f'Password: {password}\n\n'
        f'Please sign in and change your password after first login.'
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True)


def create_activity(
    activity_type: str,
    *,
    user=None,
    task: Task | None = None,
    project: Project | None = None,
    title: str = '',
    message: str,
    rating: int | None = None,
    is_read: bool = False,
) -> Activity:
    return Activity.objects.create(
        activity_type=activity_type,
        user=user,
        task=task,
        project=project,
        title=title,
        message=message,
        rating=rating,
        is_read=is_read,
    )


def create_notification(recipient, title: str, message: str) -> None:
    if recipient is None:
        return
    create_activity(
        Activity.Type.NOTIFICATION,
        user=recipient,
        title=title,
        message=message,
    )


def create_audit_log(actor, action: str, entity_type: str, entity_id: int, details: str = '') -> None:
    create_activity(
        Activity.Type.AUDIT,
        user=actor,
        title=f'{action} ({entity_type} #{entity_id})',
        message=details or action,
    )


def sync_project_status(project: Project | None) -> None:
    if project is None:
        return

    tasks = project.tasks.all()
    if not tasks.exists():
        project.status = Project.Status.PLANNING
    elif tasks.exclude(status=Task.Status.COMPLETED).exists():
        project.status = Project.Status.ACTIVE
    else:
        project.status = Project.Status.COMPLETED
    project.save(update_fields=['status'])


def employee_metrics(employee: Employee) -> dict[str, float | int | str]:
    completed_tasks = employee.tasks.filter(status=Task.Status.COMPLETED)
    delayed_tasks = sum(1 for task in completed_tasks if task.completed_at and task.completed_at.date() > task.deadline)
    active_tasks = employee.tasks.exclude(status=Task.Status.COMPLETED).count()

    durations = []
    for task in completed_tasks:
        if task.completed_at:
            durations.append((task.completed_at.date() - task.created_at.date()).days or 1)
    avg_completion_time = round(sum(durations) / len(durations), 2) if durations else 0.0

    today = timezone.localdate()
    seven_days_ago = today - timedelta(days=6)
    daily_hours = (
        employee.task_logs.filter(log_date__gte=seven_days_ago)
        .values('log_date')
        .annotate(total_hours=Sum('hours_spent'))
    )
    average_daily_hours = (
        round(sum(float(day['total_hours'] or 0) for day in daily_hours) / len(daily_hours), 2)
        if daily_hours
        else 0.0
    )

    total_tasks = employee.tasks.count()
    completion_rate = round((completed_tasks.count() / total_tasks) * 100, 2) if total_tasks else 0.0

    burnout_risk = 'Low'
    if active_tasks >= 6 or average_daily_hours > 9:
        burnout_risk = 'High'
    elif active_tasks >= 4 or average_daily_hours > 7:
        burnout_risk = 'Medium'

    return {
        'tasks_completed': completed_tasks.count(),
        'avg_completion_time': avg_completion_time,
        'delayed_tasks': delayed_tasks,
        'active_tasks': active_tasks,
        'average_daily_hours': average_daily_hours,
        'burnout_risk': burnout_risk,
        'completion_rate': completion_rate,
    }


def dashboard_summary() -> dict[str, object]:
    all_tasks = Task.objects.select_related('assigned_to', 'project')
    all_projects = Project.objects.select_related('team')
    employees = Employee.objects.select_related('team')
    delayed_tasks = [task for task in all_tasks if task.is_delayed]
    employee_rows = [(employee, employee_metrics(employee)) for employee in employees]
    project_rows = [project_ai_summary(project) for project in all_projects]
    team_rows = [team_health_summary(team) for team in Team.objects.select_related('manager').all()]

    return {
        'total_tasks': all_tasks.count(),
        'completed_tasks': all_tasks.filter(status=Task.Status.COMPLETED).count(),
        'delayed_tasks': len(delayed_tasks),
        'total_projects': all_projects.count(),
        'total_teams': Team.objects.count(),
        'total_employees': employees.count(),
        'burnout_alerts': [(employee, data) for employee, data in employee_rows if data['burnout_risk'] != 'Low'][:6],
        'top_performers': sorted(employee_rows, key=lambda row: row[1]['completion_rate'], reverse=True)[:5],
        'recent_requests': Request.objects.select_related('employee', 'task')[:6],
        'top_risky_projects': sorted(project_rows, key=lambda row: row.delay_risk, reverse=True)[:5],
        'team_health_rows': sorted(team_rows, key=lambda row: row['health_score'])[:5],
    }


def productivity_trend(tasks) -> list[dict[str, object]]:
    rows = (
        tasks.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(total=Count('id'), completed=Count('id', filter=Q(status=Task.Status.COMPLETED)))
        .order_by('day')
    )
    return list(rows)


def predict_task_completion_hours(task: Task) -> float:
    logs = list(
        TaskLog.objects.select_related('task', 'employee')
        .values(
            'task__difficulty',
            'task__progress',
            'task__estimated_hours',
            'employee__experience',
            'hours_spent',
        )
    )

    if len(logs) >= 5:
        frame = pd.DataFrame(logs)
        X = frame[['task__difficulty', 'task__progress', 'task__estimated_hours', 'employee__experience']]
        y = frame['hours_spent']
        model = LinearRegression()
        model.fit(X, y)
        input_frame = pd.DataFrame(
            [
                {
                    'task__difficulty': task.difficulty,
                    'task__progress': task.progress,
                    'task__estimated_hours': float(task.estimated_hours),
                    'employee__experience': task.assigned_to.experience if task.assigned_to else 0,
                }
            ]
        )
        return round(max(float(model.predict(input_frame)[0]), 1.0), 2)

    baseline = float(task.estimated_hours) + (task.difficulty * 1.2)
    if task.assigned_to:
        baseline -= min(task.assigned_to.experience, 6) * 0.35
    return round(max(baseline - (task.progress * 0.05), 1.0), 2)


def predict_delay_risk(task: Task) -> dict[str, str | float]:
    rows = []
    for item in Task.objects.exclude(assigned_to__isnull=True):
        remaining_days = (item.deadline - item.created_at.date()).days
        rows.append(
            {
                'difficulty': item.difficulty,
                'progress': item.progress,
                'estimated_hours': float(item.estimated_hours),
                'experience': item.assigned_to.experience if item.assigned_to else 0,
                'remaining_days': remaining_days,
                'delayed': int(bool(item.completed_at and item.completed_at.date() > item.deadline) or item.is_delayed),
            }
        )

    remaining_days = (task.deadline - timezone.localdate()).days
    experience = task.assigned_to.experience if task.assigned_to else 0

    if len(rows) >= 8 and len({row['delayed'] for row in rows}) > 1:
        frame = pd.DataFrame(rows)
        X = frame[['difficulty', 'progress', 'estimated_hours', 'experience', 'remaining_days']]
        y = frame['delayed']
        model = RandomForestClassifier(n_estimators=120, random_state=42)
        model.fit(X, y)
        probability = model.predict_proba(
            pd.DataFrame(
                [
                    {
                        'difficulty': task.difficulty,
                        'progress': task.progress,
                        'estimated_hours': float(task.estimated_hours),
                        'experience': experience,
                        'remaining_days': remaining_days,
                    }
                ]
            )
        )[0][1]
    else:
        probability = 0.18
        if remaining_days < 0:
            probability += 0.45
        elif remaining_days <= 2:
            probability += 0.25
        if task.progress < 50:
            probability += 0.20
        if task.priority == Task.Priority.HIGH:
            probability += 0.12
        if experience <= 1:
            probability += 0.10

    probability = max(0.0, min(float(probability), 0.99))
    if probability >= 0.7:
        label = 'High'
    elif probability >= 0.4:
        label = 'Medium'
    else:
        label = 'Low'
    return {'probability': round(probability * 100, 2), 'label': label}


def recommend_employee_for_task(task: Task) -> list[AllocationSuggestion]:
    required_skills = set(task.required_skill_list)
    suggestions: list[AllocationSuggestion] = []

    employees = Employee.objects.select_related('team').all()
    if task.project and task.project.team_id:
        employees = employees.filter(team=task.project.team)

    for employee in employees:
        metric = employee_metrics(employee)
        matched_skills = len(required_skills.intersection(set(employee.skill_list)))
        skill_score = matched_skills / max(len(required_skills), 1)
        performance_score = metric['completion_rate'] / 100 if metric else 0.0
        active_task_count = employee.tasks.exclude(status=Task.Status.COMPLETED).count()
        workload_score = max(0.0, 1 - (active_task_count / 8))
        score = (skill_score * 0.5) + (performance_score * 0.3) + (workload_score * 0.2)

        suggestions.append(
            AllocationSuggestion(
                employee=employee,
                score=round(score * 100, 2),
                reasons=[
                    f'Skill match: {round(skill_score * 100)}%',
                    f'Completion rate: {round((performance_score or 0) * 100)}%',
                    f'Low workload score: {round(workload_score * 100)}%',
                ],
            )
        )

    return sorted(suggestions, key=lambda item: item.score, reverse=True)[:3]


def skill_gap_analysis(task: Task) -> dict[str, object]:
    required_skills = set(task.required_skill_list)
    employee_skills = set(task.assigned_to.skill_list) if task.assigned_to else set()
    matched_skills = sorted(required_skills.intersection(employee_skills))
    missing_skills = sorted(required_skills - employee_skills)
    learning_map = {
        'python': 'Python for Everybody',
        'django': 'Django for Beginners',
        'sql': 'SQLBolt',
        'flutter': 'Flutter Codelabs',
        'pandas': 'Pandas Foundations',
        'statistics': 'Intro to Statistics',
        'reporting': 'Business Reporting Basics',
    }
    recommended_training = [learning_map[skill] for skill in missing_skills if skill in learning_map]
    return {
        'matched_skills': matched_skills,
        'missing_skills': missing_skills,
        'recommended_training': recommended_training,
    }


def task_anomaly_analysis(task: Task) -> dict[str, object]:
    logs = list(task.logs.order_by('-log_date')[:5])
    signals: list[str] = []
    severity_score = 0

    if not logs:
        return {
            'label': 'Low',
            'score': 0,
            'signals': ['Not enough task logs yet for anomaly detection.'],
        }

    hour_values = [float(log.hours_spent) for log in logs]
    average_hours = sum(hour_values) / len(hour_values)
    max_hours = max(hour_values)

    if len(logs) >= 3 and max_hours >= max(average_hours * 1.8, 8):
        severity_score += 35
        signals.append('Recent work log shows an unusual spike in hours.')

    if task.progress <= 40 and len(logs) >= 3:
        severity_score += 25
        signals.append('Several work logs exist, but task progress is still low.')

    if task.deadline <= timezone.localdate() + timedelta(days=2) and task.progress < 70:
        severity_score += 30
        signals.append('Deadline is near while progress is still behind target.')

    if task.assigned_to:
        metrics = employee_metrics(task.assigned_to)
        if metrics['burnout_risk'] == 'High':
            severity_score += 20
            signals.append('Assigned employee is already showing high workload pressure.')

    if severity_score >= 60:
        label = 'High'
    elif severity_score >= 30:
        label = 'Medium'
    else:
        label = 'Low'

    if not signals:
        signals.append('No abnormal work pattern detected.')

    return {
        'label': label,
        'score': min(severity_score, 100),
        'signals': signals,
    }


def employee_ai_profile(employee: Employee) -> dict[str, object]:
    metrics = employee_metrics(employee)
    active_tasks = list(employee.tasks.exclude(status=Task.Status.COMPLETED))
    missing_skills = set()
    for task in active_tasks:
        gap = skill_gap_analysis(task)
        missing_skills.update(gap['missing_skills'])

    completion_component = min(metrics['completion_rate'], 100) * 0.5
    delay_penalty = min(metrics['delayed_tasks'] * 8, 30)
    workload_penalty = 20 if metrics['burnout_risk'] == 'High' else 10 if metrics['burnout_risk'] == 'Medium' else 0
    productivity_score = max(0, round(completion_component + 50 - delay_penalty - workload_penalty))

    focus_area = 'Execution is stable'
    if missing_skills:
        focus_area = f"Upskill in {', '.join(sorted(missing_skills)[:3])}"
    elif metrics['burnout_risk'] != 'Low':
        focus_area = 'Reduce workload pressure'
    elif metrics['avg_completion_time'] > 5:
        focus_area = 'Improve delivery speed'

    return {
        **metrics,
        'productivity_score': productivity_score,
        'focus_area': focus_area,
        'learning_priority': sorted(missing_skills),
    }


def project_ai_summary(project: Project) -> ProjectAIInsight:
    tasks = list(project.tasks.select_related('assigned_to').all())
    if not tasks:
        return ProjectAIInsight(
            project=project,
            health_score=100,
            delay_risk=0.0,
            completion_confidence=95,
            recommended_action='Start project planning and assign the first tasks.',
        )

    open_tasks = [task for task in tasks if task.status != Task.Status.COMPLETED]
    delay_values = [float(predict_delay_risk(task)['probability']) for task in open_tasks] or [0.0]
    average_delay = round(sum(delay_values) / len(delay_values), 2)
    completion_confidence = max(5, round(100 - average_delay - (len(open_tasks) * 4)))
    health_score = max(0, round(project.progress * 0.5 + completion_confidence * 0.5))

    if average_delay >= 65:
        recommended_action = 'Reassign high-risk tasks and review deadlines immediately.'
    elif average_delay >= 40:
        recommended_action = 'Monitor closely and support the tasks with medium delay risk.'
    elif project.progress < 35 and open_tasks:
        recommended_action = 'Increase execution focus and close the early backlog.'
    else:
        recommended_action = 'Project is on a healthy track.'

    return ProjectAIInsight(
        project=project,
        health_score=health_score,
        delay_risk=average_delay,
        completion_confidence=completion_confidence,
        recommended_action=recommended_action,
    )


def team_health_summary(team: Team) -> dict[str, object]:
    employees = list(team.employees.all())
    if not employees:
        return {
            'team': team,
            'health_score': 100,
            'burnout_count': 0,
            'active_tasks': 0,
            'recommended_action': 'Assign employees to begin active delivery.',
        }

    profiles = [employee_ai_profile(employee) for employee in employees]
    avg_productivity = sum(profile['productivity_score'] for profile in profiles) / len(profiles)
    burnout_count = sum(1 for profile in profiles if profile['burnout_risk'] != 'Low')
    active_tasks = sum(profile['active_tasks'] for profile in profiles)
    health_score = max(0, round(avg_productivity - (burnout_count * 8)))

    if burnout_count >= max(1, len(employees) // 2):
        action = 'Balance workload across the team and review deadlines.'
    elif active_tasks >= len(employees) * 4:
        action = 'Watch capacity closely; the team is carrying a heavy delivery load.'
    else:
        action = 'Team health is stable. Maintain current execution rhythm.'

    return {
        'team': team,
        'health_score': health_score,
        'burnout_count': burnout_count,
        'active_tasks': active_tasks,
        'recommended_action': action,
    }


def upsert_ai_result(task: Task) -> AIResult:
    predicted_time = predict_task_completion_hours(task)
    delay_risk = predict_delay_risk(task)
    skill_gap = skill_gap_analysis(task)

    if task.assigned_to:
        metrics = employee_metrics(task.assigned_to)
        burnout_signal = metrics['burnout_risk']
    else:
        burnout_signal = 'Unassigned'

    recommended_action = 'Proceed as planned'
    if delay_risk['label'] == 'High':
        recommended_action = 'Rebalance workload or extend the deadline'
    elif skill_gap['missing_skills']:
        recommended_action = 'Provide support or training for missing skills'

    ai_result, _ = AIResult.objects.update_or_create(
        task=task,
        defaults={
            'predicted_time': predicted_time,
            'delay_risk': float(delay_risk['probability']),
            'suggestions': ', '.join(skill_gap['missing_skills']) or 'Current assignment is a strong fit.',
            'recommended_action': recommended_action,
            'workload_signal': burnout_signal,
        },
    )
    return ai_result
