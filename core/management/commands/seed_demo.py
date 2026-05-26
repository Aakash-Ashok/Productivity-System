from datetime import timedelta
from decimal import Decimal
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Activity, AppRole, Employee, Project, Request, Task, TaskLog, Team, assign_user_role
from core.services import create_audit_log, create_notification, upsert_ai_result


class Command(BaseCommand):
    help = 'Seed the database with simplified demo users, teams, projects, tasks, requests, and activity.'

    def handle(self, *args, **options):
        today = timezone.localdate()

        admin_user, created = User.objects.get_or_create(
            username='admin',
            defaults={'email': 'admin@example.com'},
        )
        if created:
            admin_user.set_password('admin123')
            admin_user.save()
        assign_user_role(admin_user, AppRole.ADMIN)

        manager_specs = [
            ('manager1', 'manager1@example.com', 'manager123'),
            ('manager2', 'manager2@example.com', 'manager123'),
        ]
        managers = []
        for username, email, password in manager_specs:
            manager, created = User.objects.get_or_create(username=username, defaults={'email': email})
            if created:
                manager.set_password(password)
                manager.save()
            assign_user_role(manager, AppRole.MANAGER)
            managers.append(manager)

        teams = [
            Team.objects.get_or_create(name='Platform Team', defaults={'manager': managers[0], 'description': 'Backend and AI delivery team.'})[0],
            Team.objects.get_or_create(name='Experience Team', defaults={'manager': managers[1], 'description': 'UI and reporting team.'})[0],
        ]

        employee_specs = [
            ('ava', 'ava@example.com', 'emp123', 'Ava Patel', 'Backend Engineer', 'python,django,sql,ml', 5, teams[0]),
            ('noah', 'noah@example.com', 'emp123', 'Noah Kim', 'Frontend Developer', 'html,css,javascript,reporting', 4, teams[1]),
            ('mia', 'mia@example.com', 'emp123', 'Mia Lopez', 'Data Analyst', 'python,pandas,statistics,sql', 3, teams[0]),
            ('liam', 'liam@example.com', 'emp123', 'Liam Chen', 'Project Coordinator', 'planning,documentation,communication', 6, teams[1]),
        ]

        employees = []
        for username, email, password, name, title, skills, experience, team in employee_specs:
            user, created = User.objects.get_or_create(username=username, defaults={'email': email})
            if created:
                user.set_password(password)
                user.save()
            assign_user_role(user, AppRole.EMPLOYEE)
            employee, _ = Employee.objects.get_or_create(
                email=email,
                defaults={
                    'user': user,
                    'team': team,
                    'name': name,
                    'job_title': title,
                    'skills': skills,
                    'experience': experience,
                    'weekly_capacity_hours': Decimal('40.00'),
                },
            )
            employee.user = user
            employee.team = team
            employee.name = name
            employee.job_title = title
            employee.skills = skills
            employee.experience = experience
            employee.save()
            employees.append(employee)

        projects = [
            Project.objects.get_or_create(
                name='AI Productivity Suite',
                team=teams[0],
                defaults={
                    'description': 'Core platform and prediction modules.',
                    'deadline': today + timedelta(days=30),
                    'status': Project.Status.ACTIVE,
                },
            )[0],
            Project.objects.get_or_create(
                name='Manager Insight Portal',
                team=teams[1],
                defaults={
                    'description': 'Dashboards and reporting for managers.',
                    'deadline': today + timedelta(days=45),
                    'status': Project.Status.ACTIVE,
                },
            )[0],
        ]

        task_specs = [
            ('Build role-based login', projects[0], employees[0], 'python,django,auth', 3, 14),
            ('Train delay-risk model', projects[0], employees[2], 'python,pandas,statistics', 5, 18),
            ('Create manager dashboard UI', projects[1], employees[1], 'html,css,javascript', 4, 20),
            ('Prepare project tracking report', projects[1], employees[3], 'documentation,reporting,planning', 2, 10),
        ]

        created_tasks = []
        for index, (title, project, employee, skills, difficulty, estimated_hours) in enumerate(task_specs):
            task, _ = Task.objects.get_or_create(
                title=title,
                project=project,
                defaults={
                    'description': f'{title} for {project.name}.',
                    'assigned_to': employee,
                    'created_by': admin_user,
                    'required_skills': skills,
                    'deadline': today + timedelta(days=7 + index * 3),
                    'priority': random.choice([Task.Priority.MEDIUM, Task.Priority.HIGH]),
                    'status': random.choice([Task.Status.PENDING, Task.Status.IN_PROGRESS]),
                    'difficulty': difficulty,
                    'requires_approval': index % 2 == 0,
                    'estimated_hours': Decimal(str(estimated_hours)),
                    'progress': random.choice([20, 40, 60, 80]),
                },
            )
            created_tasks.append(task)
            if not task.logs.exists():
                for days_back in range(3):
                    TaskLog.objects.create(
                        task=task,
                        employee=employee,
                        hours_spent=Decimal(str(random.choice([2, 3.5, 4, 5]))),
                        log_date=today - timedelta(days=days_back),
                        notes='Demo work log entry.',
                    )
            upsert_ai_result(task)

        if created_tasks:
            approval_task = created_tasks[0]
            approval_task.progress = 100
            approval_task.requires_approval = True
            approval_task.save()
            Request.objects.get_or_create(
                request_type=Request.Type.TASK_APPROVAL,
                task=approval_task,
                employee=approval_task.assigned_to,
                defaults={
                    'raised_by': approval_task.assigned_to.user if approval_task.assigned_to else admin_user,
                    'status': Request.Status.PENDING,
                    'remarks': 'Submitted for review.',
                },
            )

        Request.objects.get_or_create(
            request_type=Request.Type.LEAVE,
            employee=employees[1],
            defaults={
                'raised_by': employees[1].user,
                'start_date': today + timedelta(days=3),
                'end_date': today + timedelta(days=5),
                'status': Request.Status.PENDING,
                'remarks': 'Personal leave request.',
            },
        )

        Activity.objects.get_or_create(
            activity_type=Activity.Type.REVIEW,
            user=employees[0].user,
            title='Quarterly review',
            defaults={'message': 'Consistent delivery and good code quality.', 'rating': 4},
        )

        Activity.objects.get_or_create(
            activity_type=Activity.Type.REVIEW,
            user=employees[2].user,
            title='Performance review - Mia Lopez',
            defaults={'message': 'Strong analytical output and dependable reporting support.', 'rating': 5},
        )

        if len(created_tasks) >= 3:
            Activity.objects.get_or_create(
                activity_type=Activity.Type.COMMENT,
                task=created_tasks[0],
                project=created_tasks[0].project,
                user=managers[0],
                title='Manager comment on task',
                defaults={'message': 'Please keep the authentication flow simple and document the role checks clearly.'},
            )
            Activity.objects.get_or_create(
                activity_type=Activity.Type.COMMENT,
                task=created_tasks[1],
                project=created_tasks[1].project,
                user=employees[2].user,
                title='Progress update comment',
                defaults={'message': 'Initial data cleanup is done. Model training will start after final feature review.'},
            )
            Activity.objects.get_or_create(
                activity_type=Activity.Type.COMMENT,
                task=created_tasks[2],
                project=created_tasks[2].project,
                user=employees[1].user,
                title='UI implementation note',
                defaults={'message': 'Dashboard cards are drafted. I am polishing spacing and responsive behavior next.'},
            )

        create_notification(employees[0].user, 'Task assignment alert', 'Check your dashboard for newly assigned work.')
        create_notification(managers[0], 'Pending approval', 'A task is waiting in your approval queue.')
        create_notification(managers[0], 'Deadline warning', 'One team task is approaching its deadline this week.')
        create_notification(managers[1], 'Leave request submitted', 'A team member has submitted a leave request for review.')
        create_notification(employees[1].user, 'Leave request update', 'Your leave request has been recorded and is awaiting manager review.')
        create_notification(employees[2].user, 'AI insight generated', 'A fresh AI prediction is available for your assigned task.')
        create_notification(admin_user, 'System activity summary', 'New requests and task activity were generated in the demo workspace.')

        create_audit_log(admin_user, 'seeded_demo_data', 'System', 1, 'Demo users, requests, and activities were refreshed.')
        if created_tasks:
            create_audit_log(managers[0], 'reviewed_task_scope', 'Task', created_tasks[0].id, created_tasks[0].title)
            create_audit_log(admin_user, 'monitored_requests', 'Request', 1, 'Admin reviewed current demo request volume.')

        self.stdout.write(
            self.style.SUCCESS(
                'Demo data ready. Admin login: admin/admin123, Manager login: manager1/manager123, Employee login: ava/emp123'
            )
        )
