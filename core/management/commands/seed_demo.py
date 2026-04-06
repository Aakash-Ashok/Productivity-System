from datetime import timedelta
from decimal import Decimal
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Employee, LeaveRequest, Milestone, Notification, PerformanceReview, Project, RecurringTask, Task, TaskLog, Team, UserProfile
from core.services import create_notification, refresh_performance_metrics, upsert_ai_result


class Command(BaseCommand):
    help = 'Seed the database with demo users, teams, projects, tasks, and logs.'

    def handle(self, *args, **options):
        today = timezone.localdate()

        admin_user, created = User.objects.get_or_create(
            username='admin',
            defaults={'email': 'admin@example.com', 'is_staff': True, 'is_superuser': True},
        )
        if created:
            admin_user.set_password('admin123')
            admin_user.save()
        UserProfile.objects.update_or_create(user=admin_user, defaults={'role': UserProfile.Role.ADMIN})

        manager_specs = [
            ('manager1', 'manager1@example.com', 'manager123', 'Engineering Manager'),
            ('manager2', 'manager2@example.com', 'manager123', 'Delivery Manager'),
        ]
        managers = []
        for username, email, password, _title in manager_specs:
            manager, created = User.objects.get_or_create(username=username, defaults={'email': email, 'is_staff': True})
            if created:
                manager.set_password(password)
                manager.save()
            UserProfile.objects.update_or_create(user=manager, defaults={'role': UserProfile.Role.MANAGER})
            managers.append(manager)

        employee_specs = [
            ('ava', 'ava@example.com', 'emp123', 'Ava Patel', 'Backend Engineer', 'python,django,sql,ml', 5),
            ('noah', 'noah@example.com', 'emp123', 'Noah Kim', 'Mobile Developer', 'flutter,dart,ui,testing', 4),
            ('mia', 'mia@example.com', 'emp123', 'Mia Lopez', 'Data Analyst', 'python,pandas,reporting,statistics', 3),
            ('liam', 'liam@example.com', 'emp123', 'Liam Chen', 'Project Coordinator', 'planning,documentation,communication', 6),
        ]

        teams = [
            Team.objects.get_or_create(name='Platform Team', defaults={'manager': managers[0], 'description': 'Backend and AI delivery team.'})[0],
            Team.objects.get_or_create(name='Experience Team', defaults={'manager': managers[1], 'description': 'UI, mobile, and reporting team.'})[0],
        ]

        employees = []
        for index, (username, email, password, name, title, skills, experience) in enumerate(employee_specs):
            user, created = User.objects.get_or_create(username=username, defaults={'email': email})
            if created:
                user.set_password(password)
                user.save()
            UserProfile.objects.update_or_create(user=user, defaults={'role': UserProfile.Role.EMPLOYEE})
            employee, _ = Employee.objects.get_or_create(
                email=email,
                defaults={
                    'user': user,
                    'team': teams[index % len(teams)],
                    'name': name,
                    'job_title': title,
                    'skills': skills,
                    'experience_years': experience,
                    'weekly_capacity_hours': 40,
                },
            )
            if employee.user_id != user.id or employee.team_id is None:
                employee.user = user
                employee.team = teams[index % len(teams)]
                employee.save()
            employees.append(employee)

        projects = [
            Project.objects.get_or_create(
                name='AI Productivity Suite',
                defaults={
                    'team': teams[0],
                    'description': 'Core platform, task engine, and AI prediction modules.',
                    'deadline': today + timedelta(days=30),
                    'status': Project.Status.ACTIVE,
                },
            )[0],
            Project.objects.get_or_create(
                name='Manager Insight Portal',
                defaults={
                    'team': teams[1],
                    'description': 'Dashboards, analytics, and reporting views for leadership.',
                    'deadline': today + timedelta(days=45),
                    'status': Project.Status.ACTIVE,
                },
            )[0],
        ]

        milestone, _ = Milestone.objects.get_or_create(
            project=projects[0],
            name='Phase 1 Delivery',
            defaults={'description': 'First phase milestone', 'deadline': today + timedelta(days=14), 'status': Project.Status.ACTIVE},
        )

        task_specs = [
            ('Build role-based login', projects[0], employees[0], 'python,django,auth', 3, 14),
            ('Train delay-risk model', projects[0], employees[2], 'python,pandas,statistics', 5, 18),
            ('Create team dashboard UI', projects[1], employees[1], 'flutter,ui,reporting', 4, 20),
            ('Prepare project tracking reports', projects[1], employees[3], 'documentation,reporting,planning', 2, 10),
        ]

        for index, (title, project, employee, skills, difficulty, estimated_hours) in enumerate(task_specs):
            task, _ = Task.objects.get_or_create(
                title=title,
                defaults={
                    'project': project,
                    'milestone': milestone if project == projects[0] else None,
                    'description': f'{title} for {project.name}.',
                    'assigned_to': employee,
                    'created_by': admin_user,
                    'required_skills': skills,
                    'deadline': today + timedelta(days=7 + index * 2),
                    'priority': random.choice([Task.Priority.MEDIUM, Task.Priority.HIGH, Task.Priority.CRITICAL]),
                    'status': random.choice([Task.Status.TODO, Task.Status.IN_PROGRESS, Task.Status.REVIEW]),
                    'difficulty': difficulty,
                    'requires_approval': index % 2 == 0,
                    'estimated_hours': Decimal(str(estimated_hours)),
                    'progress': random.choice([20, 40, 60, 80]),
                },
            )
            if not task.logs.exists():
                for days_back in range(3):
                    TaskLog.objects.create(
                        task=task,
                        employee=employee,
                        hours_spent=Decimal(str(random.choice([2, 3.5, 4, 5]))),
                        log_date=today - timedelta(days=days_back),
                        notes='Demo work log entry generated for presentation.',
                        progress_after_log=min(task.progress, (days_back + 1) * 25),
                    )
            upsert_ai_result(task)

        RecurringTask.objects.get_or_create(
            project=projects[1],
            title='Weekly team status report',
            defaults={
                'description': 'Prepare the weekly status summary.',
                'assigned_to': employees[3],
                'frequency': RecurringTask.Frequency.WEEKLY,
                'next_due_date': today + timedelta(days=7),
                'is_active': True,
            },
        )

        PerformanceReview.objects.get_or_create(
            employee=employees[0],
            manager=managers[0],
            review_date=today,
            defaults={'rating': 4, 'remarks': 'Consistent delivery and good code quality.'},
        )

        if employees[0].user:
            create_notification(
                employees[0].user,
                'Task assignment alert',
                'Check your dashboard for newly assigned work.',
                Notification.Kind.TASK,
            )
        create_notification(
            managers[0],
            'Deadline warning',
            'One of your team tasks is approaching its deadline.',
            Notification.Kind.DEADLINE,
        )

        LeaveRequest.objects.get_or_create(
            employee=employees[1],
            start_date=today + timedelta(days=3),
            end_date=today + timedelta(days=5),
            defaults={'reason': 'Personal leave', 'status': LeaveRequest.Status.PENDING},
        )

        refresh_performance_metrics()
        self.stdout.write(
            self.style.SUCCESS(
                'Demo data ready. Admin login: admin/admin123, Manager login: manager1/manager123, Employee login: ava/emp123'
            )
        )
