from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression

from .models import AIResult, AuditLog, Employee, Notification, PerformanceMetric, Project, Task, TaskLog, Team


@dataclass
class AllocationSuggestion:
    employee: Employee
    score: float
    reasons: list[str]


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


def create_notification(recipient, title: str, message: str, kind: str = Notification.Kind.SYSTEM) -> None:
    if recipient is None:
        return
    Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        kind=kind,
    )


def create_audit_log(actor, action: str, entity_type: str, entity_id: int, details: str = '') -> None:
    AuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
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


def refresh_performance_metrics() -> None:
    today = timezone.localdate()
    seven_days_ago = today - timedelta(days=6)

    for employee in Employee.objects.select_related('team').all():
        completed_tasks = employee.tasks.filter(status=Task.Status.COMPLETED)
        delayed_tasks = completed_tasks.filter(completed_at__date__gt=F('deadline')).count()
        active_tasks = employee.tasks.exclude(status=Task.Status.COMPLETED).count()

        durations = []
        for task in completed_tasks:
            if task.completed_at:
                durations.append((task.completed_at.date() - task.created_at.date()).days or 1)
        avg_completion_time = round(sum(durations) / len(durations), 2) if durations else 0.0

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

        PerformanceMetric.objects.update_or_create(
            employee=employee,
            defaults={
                'tasks_completed': completed_tasks.count(),
                'avg_completion_time': avg_completion_time,
                'delayed_tasks': delayed_tasks,
                'active_tasks': active_tasks,
                'average_daily_hours': average_daily_hours,
                'burnout_risk': burnout_risk,
                'completion_rate': completion_rate,
            },
        )


def predict_task_completion_hours(task: Task) -> float:
    logs = list(
        TaskLog.objects.select_related('task', 'employee')
        .values(
            'task__difficulty',
            'task__progress',
            'task__estimated_hours',
            'employee__experience_years',
            'hours_spent',
        )
    )

    if len(logs) >= 5:
        frame = pd.DataFrame(logs)
        X = frame[['task__difficulty', 'task__progress', 'task__estimated_hours', 'employee__experience_years']]
        y = frame['hours_spent']
        model = LinearRegression()
        model.fit(X, y)
        input_frame = pd.DataFrame(
            [
                {
                    'task__difficulty': task.difficulty,
                    'task__progress': task.progress,
                    'task__estimated_hours': float(task.estimated_hours),
                    'employee__experience_years': task.assigned_to.experience_years if task.assigned_to else 0,
                }
            ]
        )
        return round(max(float(model.predict(input_frame)[0]), 1.0), 2)

    baseline = float(task.estimated_hours) + (task.difficulty * 1.2)
    if task.assigned_to:
        baseline -= min(task.assigned_to.experience_years, 6) * 0.35
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
                'experience_years': item.assigned_to.experience_years if item.assigned_to else 0,
                'remaining_days': remaining_days,
                'delayed': int(bool(item.completed_at and item.completed_at.date() > item.deadline) or item.is_delayed),
            }
        )

    remaining_days = (task.deadline - timezone.localdate()).days
    experience = task.assigned_to.experience_years if task.assigned_to else 0

    if len(rows) >= 8 and len({row['delayed'] for row in rows}) > 1:
        frame = pd.DataFrame(rows)
        X = frame[['difficulty', 'progress', 'estimated_hours', 'experience_years', 'remaining_days']]
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
                        'experience_years': experience,
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
        if task.priority == Task.Priority.CRITICAL:
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
    refresh_performance_metrics()
    required_skills = set(task.required_skill_list)
    suggestions: list[AllocationSuggestion] = []

    employees = Employee.objects.select_related('team', 'performance_metric').all()
    if task.project and task.project.team_id:
        employees = employees.filter(team=task.project.team)

    for employee in employees:
        metric = getattr(employee, 'performance_metric', None)
        matched_skills = len(required_skills.intersection(set(employee.skill_list)))
        skill_score = matched_skills / max(len(required_skills), 1)
        performance_score = (metric.completion_rate / 100) if metric else 0.0
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
        'pandas': 'Pandas official tutorials',
        'statistics': 'Intro to Statistics',
        'testing': 'Software Testing Fundamentals',
        'reporting': 'Data Visualization Basics',
    }
    learning_recommendations = [learning_map[skill] for skill in missing_skills if skill in learning_map]
    return {
        'matched_skills': matched_skills,
        'missing_skills': missing_skills,
        'learning_recommendations': learning_recommendations,
    }


def recommended_task_action(task: Task, delay_risk: dict[str, str | float]) -> str:
    if task.assigned_to is None:
        return 'Assign the task using smart allocation.'
    if delay_risk['label'] == 'High':
        return 'Escalate, split scope, or extend deadline immediately.'
    if getattr(task.assigned_to, 'performance_metric', None) and task.assigned_to.performance_metric.burnout_risk == 'High':
        return 'Consider reassigning or reducing workload for the assignee.'
    if task.progress < 25:
        return 'Schedule a manager check-in and request a progress update.'
    return 'Continue monitoring current progress.'


def upsert_ai_result(task: Task) -> AIResult:
    predicted_time = predict_task_completion_hours(task)
    delay_risk = predict_delay_risk(task)
    suggestions = recommend_employee_for_task(task)
    skill_gap = skill_gap_analysis(task)
    workload_signal = 'Balanced'
    burnout_score = 0.0
    if task.assigned_to and hasattr(task.assigned_to, 'performance_metric'):
        workload_signal = task.assigned_to.performance_metric.burnout_risk
        burnout_score = task.assigned_to.performance_metric.average_daily_hours
    suggestion_text = ', '.join(f'{item.employee.name} ({item.score}%)' for item in suggestions)
    ai_result, _ = AIResult.objects.update_or_create(
        task=task,
        defaults={
            'predicted_time': predicted_time,
            'delay_risk': delay_risk['probability'],
            'suggestions': suggestion_text,
            'recommended_action': recommended_task_action(task, delay_risk),
            'skill_gap': ', '.join(skill_gap['missing_skills']),
            'learning_recommendations': ', '.join(skill_gap['learning_recommendations']),
            'workload_signal': workload_signal,
            'burnout_score': burnout_score,
        },
    )
    return ai_result


def team_performance_snapshot():
    teams = Team.objects.annotate(
        member_count=Count('employees', distinct=True),
        project_count=Count('projects', distinct=True),
        active_tasks=Count('employees__tasks', filter=~Q(employees__tasks__status=Task.Status.COMPLETED), distinct=True),
        completed_tasks=Count('employees__tasks', filter=Q(employees__tasks__status=Task.Status.COMPLETED), distinct=True),
    ).order_by('name')
    snapshots = []
    for team in teams:
        metrics = PerformanceMetric.objects.filter(employee__team=team)
        avg_completion_rate = round(sum(metric.completion_rate for metric in metrics) / len(metrics), 2) if metrics else 0
        avg_delay = round(sum(metric.delayed_tasks for metric in metrics) / len(metrics), 2) if metrics else 0
        health_score = round(max(0, 100 - (avg_delay * 8) - max(0, 70 - avg_completion_rate)), 2)
        snapshots.append(
            {
                'team': team,
                'member_count': team.member_count,
                'project_count': team.project_count,
                'active_tasks': team.active_tasks,
                'completed_tasks': team.completed_tasks,
                'health_score': health_score,
                'avg_completion_rate': avg_completion_rate,
            }
        )
    return snapshots


def project_progress_snapshot():
    projects = Project.objects.select_related('team').prefetch_related('tasks')
    return [
        {
            'project': project,
            'progress': project.progress,
            'active_tasks': project.tasks.exclude(status=Task.Status.COMPLETED).count(),
            'completed_tasks': project.tasks.filter(status=Task.Status.COMPLETED).count(),
        }
        for project in projects
    ]


def productivity_trend(days: int = 7) -> list[dict[str, object]]:
    today = timezone.localdate()
    trend = []
    for offset in range(days - 1, -1, -1):
        current = today - timedelta(days=offset)
        total_hours = TaskLog.objects.filter(log_date=current).aggregate(total=Sum('hours_spent'))['total'] or 0
        completed = Task.objects.filter(completed_at__date=current).count()
        trend.append(
            {
                'date': current.strftime('%Y-%m-%d'),
                'hours': float(total_hours),
                'completed': completed,
            }
        )
    return trend


def dashboard_summary() -> dict[str, object]:
    refresh_performance_metrics()
    today = timezone.localdate()

    total_tasks = Task.objects.count()
    completed_tasks = Task.objects.filter(status=Task.Status.COMPLETED).count()
    delayed_tasks = Task.objects.filter(deadline__lt=today).exclude(status=Task.Status.COMPLETED).count()
    active_tasks = Task.objects.exclude(status=Task.Status.COMPLETED).count()
    top_performers = PerformanceMetric.objects.select_related('employee').order_by('-completion_rate', 'avg_completion_time')[:5]
    burnout_alerts = PerformanceMetric.objects.select_related('employee').exclude(burnout_risk='Low')
    anomaly_tasks = (
        Task.objects.annotate(total_logged=Sum('logs__hours_spent'))
        .filter(Q(total_logged__gt=F('estimated_hours') * 2) | Q(progress__lt=20, deadline__lte=today + timedelta(days=1)))
        .distinct()[:5]
    )

    return {
        'total_users': Employee.objects.count(),
        'total_teams': Team.objects.count(),
        'total_projects': Project.objects.count(),
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'delayed_tasks': delayed_tasks,
        'active_tasks': active_tasks,
        'completion_rate': round((completed_tasks / total_tasks) * 100, 2) if total_tasks else 0,
        'top_performers': top_performers,
        'burnout_alerts': burnout_alerts,
        'anomaly_tasks': anomaly_tasks,
        'upcoming_deadlines': Task.objects.exclude(status=Task.Status.COMPLETED).select_related('project', 'assigned_to').order_by('deadline')[:5],
        'workload_chart': list(
            Employee.objects.annotate(active_count=Count('tasks', filter=~Q(tasks__status=Task.Status.COMPLETED)))
            .values('name', 'active_count')
            .order_by('-active_count')
        ),
        'team_summary': team_performance_snapshot(),
        'project_summary': project_progress_snapshot(),
        'productivity_trend': productivity_trend(),
    }
