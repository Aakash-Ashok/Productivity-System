from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from core.models import AppRole, Activity, Employee, Team, Project, Task, assign_user_role
from core.services import create_notification
import datetime

class NotificationPrivacyTest(TestCase):
    def setUp(self):
        # Create users for all three roles
        self.admin_user = User.objects.create_user(username='admin_test', password='password123')
        assign_user_role(self.admin_user, AppRole.ADMIN)

        self.manager_user = User.objects.create_user(username='manager_test', password='password123')
        assign_user_role(self.manager_user, AppRole.MANAGER)

        self.employee_user = User.objects.create_user(username='employee_test', password='password123')
        assign_user_role(self.employee_user, AppRole.EMPLOYEE)

        self.other_employee_user = User.objects.create_user(username='other_employee_test', password='password123')
        assign_user_role(self.other_employee_user, AppRole.EMPLOYEE)

        # Create linked employee profiles
        self.employee_profile = Employee.objects.create(
            user=self.employee_user,
            name="Employee Test",
            email="employee@test.com",
            experience=3,
        )
        self.other_employee_profile = Employee.objects.create(
            user=self.other_employee_user,
            name="Other Employee Test",
            email="other_employee@test.com",
            experience=2,
        )

        # Create notifications for each user
        create_notification(self.admin_user, "Admin Alert", "Notification for Admin only")
        create_notification(self.manager_user, "Manager Alert", "Notification for Manager only")
        create_notification(self.employee_user, "Employee Alert", "Notification for Employee only")
        create_notification(self.other_employee_user, "Other Employee Alert", "Notification for Other Employee only")

        # Create a team, project, and task
        self.team = Team.objects.create(name="Team A", manager=self.manager_user)
        self.employee_profile.team = self.team
        self.employee_profile.save()

        self.project = Project.objects.create(name="Project A", team=self.team, deadline=datetime.date.today())
        self.task = Task.objects.create(
            project=self.project,
            title="Task A",
            assigned_to=self.employee_profile,
            deadline=datetime.date.today(),
            progress=50
        )

    def test_dashboard_notification_privacy_for_admin(self):
        # Admin logged in
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        notifications = response.context['notifications']
        
        # Admin should only see notifications created for them
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title, "Admin Alert")

    def test_dashboard_notification_privacy_for_manager(self):
        # Manager logged in
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        notifications = response.context['notifications']
        
        # Manager should only see notifications created for them
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title, "Manager Alert")

    def test_dashboard_notification_privacy_for_employee(self):
        # Employee logged in
        self.client.force_login(self.employee_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        notifications = response.context['notifications']
        
        # Employee should only see notifications created for them
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title, "Employee Alert")

    def test_notification_list_privacy_for_admin(self):
        # Admin logged in
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('notification-list'))
        self.assertEqual(response.status_code, 200)
        notifications = response.context['notifications']
        
        # Admin should only see notifications created for them
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title, "Admin Alert")

    def test_notification_list_privacy_for_manager(self):
        # Manager logged in
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('notification-list'))
        self.assertEqual(response.status_code, 200)
        notifications = response.context['notifications']
        
        # Manager should only see notifications created for them
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title, "Manager Alert")

    def test_notification_list_privacy_for_employee(self):
        # Employee logged in
        self.client.force_login(self.employee_user)
        response = self.client.get(reverse('notification-list'))
        self.assertEqual(response.status_code, 200)
        notifications = response.context['notifications']
        
        # Employee should only see notifications created for them
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title, "Employee Alert")

    def test_admin_dashboard_rendering(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['dashboard_role'], AppRole.ADMIN)
        self.assertIn('team_summary', response.context)
        self.assertIn('project_summary', response.context)
        self.assertIn('anomaly_tasks', response.context)
        self.assertIn('upcoming_deadlines', response.context)
        self.assertIn('workload_chart', response.context)

    def test_manager_dashboard_rendering(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['dashboard_role'], AppRole.MANAGER)
        self.assertIn('managed_teams', response.context)
        self.assertIn('managed_projects', response.context)
        self.assertIn('team_members', response.context)
        self.assertIn('manager_risks', response.context)

    def test_employee_dashboard_rendering(self):
        self.client.force_login(self.employee_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['dashboard_role'], AppRole.EMPLOYEE)
        self.assertIn('my_tasks', response.context)
        self.assertIn('my_logs', response.context)
        self.assertIn('my_ai_results', response.context)





from django.core.mail import send_mail

send_mail(
    "Test Email",
    "Hello from Django",
    "akashask2012@gmail.com",
    ["akashashok30@outlook.com"],
    fail_silently=False
)