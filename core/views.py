from multiprocessing import context

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from .forms import (
    EmployeeForm,
    EmployeeUpdateForm,
    LeaveApprovalForm,
    LeaveRequestForm,
    PerformanceReviewForm,
    ProfileForm,
    ProjectForm,
    TaskApprovalForm,
    TaskAttachmentForm,
    TaskCommentForm,
    TaskForm,
    TaskLogForm,
    TaskUpdateForm,
    TeamForm,
    UserAdminEditForm,
    UserAccountForm,
)
from .models import Activity, AppRole, Attachment, Employee, Project, Request, Task, TaskLog, Team, current_user_role
from .services import (
    create_activity,
    create_audit_log,
    create_notification,
    dashboard_summary,
    employee_metrics,
    employee_ai_profile,
    predict_delay_risk,
    predict_task_completion_hours,
    project_ai_summary,
    recommend_employee_for_task,
    send_credentials_email,
    skill_gap_analysis,
    sync_project_status,
    task_anomaly_analysis,
    team_health_summary,
    upsert_ai_result,
    team_analytics,
    
)


def get_employee_for_user(user):
    return getattr(user, 'employee_profile', None)


def get_visible_teams(user):
    role = current_user_role(user)
    if role == AppRole.ADMIN:
        return Team.objects.select_related('manager').all()
    if role == AppRole.MANAGER:
        return Team.objects.select_related('manager').filter(manager=user)
    employee = get_employee_for_user(user)
    if employee and employee.team_id:
        return Team.objects.select_related('manager').filter(pk=employee.team_id)
    return Team.objects.none()


def get_visible_employees(user):
    role = current_user_role(user)
    queryset = Employee.objects.select_related(
    'user',
    'team'
).prefetch_related(
    'tasks',
    'task_logs'
)
    if role == AppRole.ADMIN:
        return queryset.all()
    if role == AppRole.MANAGER:
        return queryset.filter(team__manager=user)
    employee = get_employee_for_user(user)
    if employee:
        return queryset.filter(pk=employee.pk)
    return queryset.none()


def get_visible_projects(user):
    role = current_user_role(user)
    queryset = Project.objects.select_related('team', 'team__manager')
    if role == AppRole.ADMIN:
        return queryset.all()
    if role == AppRole.MANAGER:
        return queryset.filter(team__manager=user)
    employee = get_employee_for_user(user)
    if employee and employee.team_id:
        return queryset.filter(team=employee.team)
    return queryset.none()


def get_visible_tasks(user):
    role = current_user_role(user)
    queryset = Task.objects.select_related(
    'project',
    'project__team',
    'assigned_to',
    'assigned_to__user'
).prefetch_related(
    'logs',
    'activities',
    'attachments',
    'requests'
)
    if role == AppRole.ADMIN:
        return queryset.all()
    if role == AppRole.MANAGER:
        return queryset.filter(project__team__manager=user)
    employee = get_employee_for_user(user)
    if employee:
        return queryset.filter(assigned_to=employee)
    return queryset.none()


def get_visible_requests(user):
    role = current_user_role(user)
    queryset = Request.objects.select_related('employee', 'employee__team', 'task', 'raised_by', 'reviewed_by')
    if role == AppRole.ADMIN:
        return queryset.all()
    if role == AppRole.MANAGER:
        return queryset.filter(Q(employee__team__manager=user) | Q(task__project__team__manager=user))
    employee = get_employee_for_user(user)
    if employee:
        return queryset.filter(employee=employee)
    return queryset.none()


def get_visible_activities(user):
    role = current_user_role(user)
    queryset = Activity.objects.select_related('user', 'task', 'project').exclude(activity_type=Activity.Type.NOTIFICATION)
    if role == AppRole.ADMIN:
        return queryset.all()
    if role == AppRole.MANAGER:
        return queryset.filter(Q(project__team__manager=user) | Q(task__project__team__manager=user) | Q(user=user))
    employee = get_employee_for_user(user)
    task_filter = Q()
    if employee:
        task_filter = Q(task__assigned_to=employee)
    return queryset.filter(task_filter | Q(user=user))


class RoleTemplateMixin:
    def get_template_names(self):
        return list(super().get_template_names())


class RoleRequiredMixin(RoleTemplateMixin, LoginRequiredMixin):
    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        role = current_user_role(request.user)
        if self.allowed_roles and role not in self.allowed_roles:
            raise PermissionDenied('You do not have permission to access this page.')
        return super().dispatch(request, *args, **kwargs)


class RoleLoginRequiredMixin(RoleTemplateMixin, LoginRequiredMixin):
    pass


class RolePasswordChangeView(RoleTemplateMixin, auth_views.PasswordChangeView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Change Password'
        return context


class RolePasswordChangeDoneView(RoleTemplateMixin, auth_views.PasswordChangeDoneView):
    pass


class DashboardView(RoleLoginRequiredMixin, TemplateView):
    def get_template_names(self):
        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return ['admin/dashboard.html']

        elif role == AppRole.MANAGER:
            return ['manager/dashboard.html']

        return ['employee/dashboard.html']
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = current_user_role(self.request.user)
        visible_tasks = get_visible_tasks(self.request.user)
        visible_projects = get_visible_projects(self.request.user)
        visible_requests = get_visible_requests(self.request.user)
        notifications = Activity.objects.filter(
            activity_type=Activity.Type.NOTIFICATION,
            user=self.request.user
        )[:6]

        context.update(
            {
                'dashboard_role': role,
                'notifications': notifications,
                'recent_tasks': visible_tasks[:6],
                'recent_requests': visible_requests[:6],
            }
        )

        if role == AppRole.ADMIN:
            summary = dashboard_summary()

            # Map burnout alerts to dictionaries for clean template access
            burnout_alerts_list = []
            for emp, data in summary['burnout_alerts']:
                burnout_alerts_list.append({
                    'employee': emp,
                    'active_tasks': data['active_tasks'],
                    'average_daily_hours': data['average_daily_hours'],
                    'burnout_risk': data['burnout_risk'],
                })

            # Map top performers to dictionaries
            top_performers_list = []
            for emp, data in summary['top_performers']:
                top_performers_list.append({
                    'employee': emp,
                    'tasks_completed': data['tasks_completed'],
                    'avg_completion_time': data['avg_completion_time'],
                    'completion_rate': data['completion_rate'],
                })

            # Get team summary metrics
            team_summary = []
            for team in Team.objects.select_related('manager').all():
                team_summary.append({
                    'team': team,
                    'member_count': team.employees.count(),
                    'project_count': team.projects.count(),
                    'active_tasks': Task.objects.filter(
                        project__team=team
                    ).exclude(
                        status=Task.Status.COMPLETED
                    ).count(),
                })

            # Get project summary metrics
            project_summary = []
            for project in Project.objects.select_related('team').all():
                project_summary.append({
                    'project': project,
                    'progress': project.progress,
                })

            # Calculate total completion rate
            completed_tasks_count = Task.objects.filter(
                status=Task.Status.COMPLETED
            ).count()

            total_tasks_count = Task.objects.count()

            completion_rate = round(
                (completed_tasks_count / total_tasks_count) * 100,
                2
            ) if total_tasks_count else 0.0

            # Workload chart data
            employees = Employee.objects.annotate(
                active_task_count=Count(
                    'tasks',
                    filter=~Q(tasks__status=Task.Status.COMPLETED)
                )
            )[:8]

            workload_chart = [
                {
                    'name': emp.name,
                    'active_count': emp.active_task_count,
                }
                for emp in employees
            ]

            context.update(summary)

            context.update({
                'approval_queue': Task.objects.filter(
                    status=Task.Status.SUBMITTED
                )[:6],
                'burnout_alerts': burnout_alerts_list,
                'top_performers': top_performers_list,
                'anomaly_tasks': [
                    task
                    for task in Task.objects.exclude(
                        status=Task.Status.COMPLETED
                    )
                    if task_anomaly_analysis(task)['label'] != 'Low'
                ][:6],
                'total_users': User.objects.count(),
                'team_summary': team_summary,
                'project_summary': project_summary,
                'upcoming_deadlines': Task.objects.exclude(
                    status=Task.Status.COMPLETED
                ).order_by('deadline')[:6],
                'workload_chart': workload_chart,
                'recent_logs': TaskLog.objects.select_related(
                    'employee',
                    'task'
                )[:6],
                'completion_rate': completion_rate,
            })

            context['teams'] = get_visible_teams(
                self.request.user
            )[:6]

        elif role == AppRole.MANAGER:
            employees = list(get_visible_employees(self.request.user))
            employee_rows = [
                (employee, employee_metrics(employee))
                for employee in employees
            ]
            managed_projects = list(visible_projects[:6])

            # Map burnout alerts for Manager
            burnout_alerts_list = []
            for emp, data in employee_rows:
                if data['burnout_risk'] != 'Low':
                    burnout_alerts_list.append({
                        'employee': emp,
                        'active_tasks': data['active_tasks'],
                        'burnout_risk': data['burnout_risk'],
                    })

            # Filter manager risks (delayed tasks)
            manager_risks = []
            for task in visible_tasks.exclude(
                status=Task.Status.COMPLETED
            ):
                risk = predict_delay_risk(task)
                if risk['label'] != 'Low':
                    manager_risks.append((task, risk))

            context.update(
                {
                    'managed_teams': get_visible_teams(self.request.user),
                    'managed_projects': visible_projects,
                    'team_members': get_visible_employees(
                        self.request.user
                    ),
                    'team_summary': employee_rows[:6],
                    'team_health_rows': [
                        team_health_summary(team)
                        for team in get_visible_teams(
                            self.request.user
                        )
                    ],
                    'managed_project_ai': [
                        project_ai_summary(project)
                        for project in managed_projects
                    ],
                    'pending_approvals': visible_tasks.filter(
                        status=Task.Status.SUBMITTED
                    )[:6],
                    'team_leave_requests': visible_requests.filter(
                        request_type=Request.Type.LEAVE
                    )[:5],
                    'burnout_alerts': burnout_alerts_list[:6],
                    'manager_risks': manager_risks[:6],
                    'managed_tasks': visible_tasks.order_by(
                        '-created_at'
                    )[:6],
                }
            )

        else:
            employee = get_employee_for_user(self.request.user)
            my_tasks = visible_tasks.order_by('deadline')

            context.update(
                {
                    'employee_record': employee,
                    'my_tasks': my_tasks,
                    'my_due_soon': my_tasks.exclude(
                        status=Task.Status.COMPLETED
                    )[:5],
                    'my_completed_tasks': my_tasks.filter(
                        status=Task.Status.COMPLETED
                    ).count(),
                    'my_pending_tasks': my_tasks.exclude(
                        status=Task.Status.COMPLETED
                    ).count(),
                    'my_logs': (
                        TaskLog.objects.filter(
                            employee=employee
                        ).select_related('task')[:8]
                        if employee else []
                    ),
                    'my_ai_results': [
                        upsert_ai_result(task)
                        for task in my_tasks[:5]
                    ],
                    'my_metric': (
                        employee_metrics(employee)
                        if employee else None
                    ),
                    'my_ai_profile': (
                        employee_ai_profile(employee)
                        if employee else None
                    ),
                }
            )

        return context

class ProfileUpdateView(RoleLoginRequiredMixin, UpdateView):
    model = User
    form_class = ProfileForm
    template_name = 'core/profile.html'
    success_url = reverse_lazy('profile')

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        user = form.save()
        create_audit_log(self.request.user, 'updated_profile', 'User', user.id, user.username)
        messages.success(self.request, 'Profile updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile_user'] = self.request.user
        context['profile_employee'] = get_employee_for_user(self.request.user)
        return context


class UserListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN,)
    model = User
    template_name = 'admin/user_list.html'
    context_object_name = 'users'

    def get_queryset(self):
        return User.objects.prefetch_related('groups').order_by('username')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user_rows'] = [
            {
                'id': account.id,
                'username': account.username,
                'email': account.email,
                'role': current_user_role(account),
                'is_staff': account.is_staff,
                'is_active': account.is_active,
            }
            for account in context['users']
        ]
        return context


class UserCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = User
    form_class = UserAccountForm
    template_name = 'admin/createuser.html'
    success_url = reverse_lazy('user-list')

    def form_valid(self, form):
        user = form.save()

        try:
            send_credentials_email(
                user.email,
                user.username,
                user.generated_password,
                user.generated_role
            )

            messages.success(
                self.request,
                'User account created and credentials emailed.'
            )

        except Exception as exc:

            messages.warning(
                self.request,
                f'User created successfully, but email delivery failed: {exc}'
            )

        create_audit_log(
            self.request.user,
            'created_user',
            'User',
            user.id,
            user.username
        )

        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create User'
        return context


class UserAdminUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN,)
    model = User
    form_class = UserAdminEditForm
    template_name = 'admin/user_update.html'
    success_url = reverse_lazy('user-list')

    def form_valid(self, form):
        user = form.save()
        create_audit_log(self.request.user, 'updated_user', 'User', user.id, user.username)
        messages.success(self.request, 'User updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit User'
        return context


class UserToggleActiveView(RoleRequiredMixin, View):
    allowed_roles = (AppRole.ADMIN,)

    def post(self, request, *args, **kwargs):
        user = get_object_or_404(User, pk=kwargs['pk'])
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        create_audit_log(request.user, 'toggled_user_active', 'User', user.id, user.username)
        messages.success(request, f'{user.username} is now {"active" if user.is_active else "inactive"}.')
        return redirect('user-list')


from django.db.models import Count

class TeamListView(RoleRequiredMixin, ListView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
    )

    context_object_name = "teams"

    def get_template_names(self):

        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return ["admin/team_list.html"]

        return ["manager/team_list.html"]

    def get_queryset(self):

        teams = get_visible_teams(
            self.request.user
        )

        return teams.annotate(
            member_count=Count(
                "employees"
            ),
            project_count=Count(
                "projects"
            )
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(
            **kwargs
        )

        context["employee_count"] = (
            Employee.objects.count()
        )

        context["manager_count"] = (
            User.objects.filter(
                managed_teams__isnull=False
            ).distinct().count()
        )

        return context


class TeamCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Team
    form_class = TeamForm
    template_name = 'admin/team_create.html'
    success_url = reverse_lazy('team-list')

    def form_valid(self, form):
        team = form.save()
        create_audit_log(self.request.user, 'created_team', 'Team', team.id, team.name)
        messages.success(self.request, 'Team created successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Team'
        return context


class TeamUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Team
    form_class = TeamForm
    template_name = 'admin/team_update.html'
    success_url = reverse_lazy('team-list')

    def form_valid(self, form):
        team = form.save()
        create_audit_log(self.request.user, 'updated_team', 'Team', team.id, team.name)
        messages.success(self.request, 'Team updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Team'
        return context


class TeamDetailView(RoleRequiredMixin, DetailView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
        AppRole.EMPLOYEE
    )

    model = Team
    context_object_name = "team"

    def get_template_names(self):

        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return ["admin/team_detail.html"]

        elif role == AppRole.MANAGER:
            return ["manager/team_detail.html"]

        return ["employee/team_detail.html"]

    def get_queryset(self):

        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return Team.objects.select_related(
                "manager"
            ).prefetch_related(
                "employees",
                "projects"
            )

        return Team.objects.select_related(
            "manager"
        ).prefetch_related(
            "employees",
            "projects"
        ).filter(
            manager=self.request.user
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        team = self.object

        projects = team.projects.all()

        employees = team.employees.all()

        active_tasks = Task.objects.filter(
            project__team=team
        ).exclude(
            status=Task.Status.COMPLETED
        ).count()

        completed_tasks = Task.objects.filter(
            project__team=team,
            status=Task.Status.COMPLETED
        ).count()

        health = team_health_summary(team)

        analytics = team_analytics(team)

        member_performance = []

        for employee in employees:

            profile = employee_ai_profile(employee)

            member_performance.append({

                "employee": employee,

                "productivity_score":
                    profile["productivity_score"],

                "completed_tasks":
                    profile["tasks_completed"],

                "active_tasks":
                    profile["active_tasks"],

                "burnout_risk":
                    profile["burnout_risk"],

            })

        context.update({

            "employees":
                employees,

            "projects":
                projects,

            "employee_count":
                employees.count(),

            "project_count":
                projects.count(),

            "active_tasks":
                active_tasks,

            "completed_tasks":
                completed_tasks,

            "team_health":
                health,

            "team_analytics":
                analytics,

            "member_performance":
                member_performance,

        })
    
        return context


from django.views.generic import FormView
from django import forms


class TeamMemberForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none()
    )

    def __init__(self, *args, **kwargs):
        team = kwargs.pop("team")
        super().__init__(*args, **kwargs)

        self.fields["employee"].queryset = Employee.objects.filter(
            team__isnull=True
        )


class TeamAddMemberView(RoleRequiredMixin, FormView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
    )

    template_name = "core/form.html"
    form_class = TeamMemberForm

    def dispatch(self, request, *args, **kwargs):

        self.team = get_object_or_404(
            Team,
            pk=kwargs["team_pk"]
        )

        if (
            current_user_role(request.user)
            == AppRole.MANAGER
            and self.team.manager != request.user
        ):
            raise PermissionDenied()

        return super().dispatch(
            request,
            *args,
            **kwargs
        )

    def get_form_kwargs(self):

        kwargs = super().get_form_kwargs()

        kwargs["team"] = self.team

        return kwargs

    def form_valid(self, form):

        employee = form.cleaned_data["employee"]

        employee.team = self.team

        employee.save()

        messages.success(
            self.request,
            "Employee added successfully."
        )

        return redirect(
            "team-detail",
            pk=self.team.pk
        )


class TeamRemoveMemberView(RoleRequiredMixin, View):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
    )

    def post(self, request, *args, **kwargs):

        team = get_object_or_404(
            Team,
            pk=kwargs["team_pk"]
        )

        if (
            current_user_role(request.user)
            == AppRole.MANAGER
            and team.manager != request.user
        ):
            raise PermissionDenied()

        employee = get_object_or_404(
            Employee,
            pk=kwargs["employee_pk"],
            team=team
        )

        employee.team = None

        employee.save()

        messages.success(
            request,
            "Employee removed from team."
        )

        return redirect(
            "team-detail",
            pk=team.pk
        )

class EmployeeListView(RoleRequiredMixin, ListView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
    )

    context_object_name = 'employees'

    def get_template_names(self):

        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return ['admin/employee_list.html']

        return ['manager/employee_list.html']

    def get_queryset(self):

        return get_visible_employees(
            self.request.user
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        employees = context['employees']

        context['employee_count'] = employees.count()

        context['available_count'] = employees.filter(
            availability=Employee.Availability.AVAILABLE
        ).count()

        context['busy_count'] = employees.filter(
            availability=Employee.Availability.BUSY
        ).count()

        context['leave_count'] = employees.filter(
            availability=Employee.Availability.ON_LEAVE
        ).count()

        return context


class EmployeeCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Employee
    form_class = EmployeeForm
    template_name = "admin/form.html"
    success_url = reverse_lazy(
        "employee-list")

    def form_valid(self, form):

        employee = form.save()

        try:

            send_credentials_email(
                employee.email,
                employee.created_user.username,
                employee.created_password,
                AppRole.EMPLOYEE
            )

        except Exception:
            pass

        create_audit_log(
            self.request.user,
            "created_employee",
            "Employee",
            employee.id,
            employee.name
        )

        messages.success(
            self.request,
            "Employee created successfully."
        )

        return redirect(
            self.success_url
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["title"] = "Create Employee"

        return context




class EmployeeUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Employee
    form_class = EmployeeUpdateForm
    template_name = 'admin/employee_update.html'
    success_url = reverse_lazy('employee-list')

    def form_valid(self, form):
        employee = form.save()
        create_audit_log(self.request.user, 'updated_employee', 'Employee', employee.id, employee.name)
        messages.success(self.request, 'Employee updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Employee'
        return context



class EmployeeDetailView(RoleRequiredMixin, DetailView):

    allowed_roles = (AppRole.ADMIN,)

    model = Employee

    context_object_name = "employee"

    template_name = "admin/employee_detail.html"

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        employee = self.object

        ai_profile = employee_ai_profile(employee)

        recent_tasks = (
            employee.tasks
            .select_related("project")
            .order_by("-created_at")[:10]
        )

        context.update({

            "ai_profile":
                ai_profile,

            "recent_tasks":
                recent_tasks,

            "total_tasks":
                employee.tasks.count(),

            "completed_tasks":
                ai_profile["tasks_completed"],

            "active_tasks":
                ai_profile["active_tasks"],

            "delayed_tasks":
                ai_profile["delayed_tasks"],

        })

        return context



class ProjectListView(RoleRequiredMixin, ListView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
        AppRole.EMPLOYEE,
    )

    model = Project

    context_object_name = "projects"

    def get_template_names(self):

        role = current_user_role(
            self.request.user
        )

        if role == AppRole.ADMIN:
            return [
                "admin/project_list.html"
            ]

        elif role == AppRole.MANAGER:
            return [
                "manager/project_list.html"
            ]

        return [
            "employee/project_list.html"
        ]

    def get_queryset(self):

        role = current_user_role(
            self.request.user
        )

        queryset = get_visible_projects(
            self.request.user
        ).select_related(
            "team",
            "team__manager"
        )

        if role == AppRole.MANAGER:

            queryset = queryset.filter(
                team__manager=self.request.user
            )

        elif role == AppRole.EMPLOYEE:

            employee = getattr(
                self.request.user,
                "employee_profile",
                None
            )

            if employee:

                queryset = queryset.filter(
                    tasks__assigned_to=employee
                ).distinct()

            else:

                queryset = Project.objects.none()

        return queryset

    def get_context_data(self, **kwargs):

        context = super().get_context_data(
            **kwargs
        )

        role = current_user_role(
            self.request.user
        )

        projects = context["projects"]

        total_projects = projects.count()

        completed_projects = projects.filter(
            status=Project.Status.COMPLETED
        ).count()

        active_projects = projects.exclude(
            status=Project.Status.COMPLETED
        ).count()

        overdue_projects = projects.filter(
            deadline__lt=timezone.now().date()
        ).exclude(
            status=Project.Status.COMPLETED
        ).count()

        context.update({

            "is_admin_role":
                role == AppRole.ADMIN,

            "is_manager_role":
                role == AppRole.MANAGER,

            "is_employee_role":
                role == AppRole.EMPLOYEE,

            "total_projects":
                total_projects,

            "completed_projects":
                completed_projects,

            "active_projects":
                active_projects,

            "overdue_projects":
                overdue_projects,

        })

        if role == AppRole.MANAGER:

            team = Team.objects.filter(
                manager=self.request.user
            ).first()

            context["managed_team"] = team

        if role == AppRole.EMPLOYEE:

            employee = getattr(
                self.request.user,
                "employee_profile",
                None
            )

            if employee:

                my_tasks = Task.objects.filter(
                    assigned_to=employee
                )

                context.update({

                    "my_tasks":
                        my_tasks.count(),

                    "my_completed_tasks":
                        my_tasks.filter(
                            status=Task.Status.COMPLETED
                        ).count(),

                    "my_pending_tasks":
                        my_tasks.exclude(
                            status=Task.Status.COMPLETED
                        ).count(),

                })

        return context


class ProjectCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Project
    form_class = ProjectForm
    template_name = 'admin/project_create.html'
    success_url = reverse_lazy('project-list')

    def form_valid(self, form):
        project = form.save()
        create_audit_log(self.request.user, 'created_project', 'Project', project.id, project.name)
        messages.success(self.request, 'Project created successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Project'
        return context


from django.http import JsonResponse
from .services import recommend_team_for_project

class ProjectRecommendationView(
    RoleRequiredMixin,
    View
):

    allowed_roles = (
        AppRole.ADMIN,
    )

    def post(
        self,
        request,
        *args,
        **kwargs
    ):

        skills = request.POST.get(
            "skills",
            ""
        )

        recommendation = (
            recommend_team_for_project(
                skills
            )
        )

        if not recommendation:

            return JsonResponse({

                "success": False

            })

        return JsonResponse({

            "success": True,

            "team":
                recommendation[
                    "team"
                ].name,

            "team_id":
                recommendation[
                    "team"
                ].id,

            "score":
                recommendation[
                    "score"
                ],

            "skill_match":
                recommendation[
                    "skill_match"
                ],

            "availability":
                recommendation[
                    "availability"
                ],

            "experience":
                recommendation[
                    "experience"
                ],

            "workload":
                recommendation[
                    "workload"
                ],
        })


class ProjectUpdateView(RoleRequiredMixin, UpdateView):

    allowed_roles = (
        AppRole.ADMIN,
    )

    model = Project

    form_class = ProjectForm

    template_name = "admin/project_update.html"

    success_url = reverse_lazy(
        "project-list"
    )

    def form_valid(self, form):

        response = super().form_valid(
            form
        )

        create_audit_log(
            self.request.user,
            "updated_project",
            "Project",
            self.object.id,
            self.object.name
        )

        messages.success(
            self.request,
            "Project updated successfully."
        )

        return response

    def get_context_data(self, **kwargs):

        context = super().get_context_data(
            **kwargs
        )

        context["title"] = (
            "Edit Project"
        )

        return context

from .services import workload_percentage

class ProjectDetailView(RoleRequiredMixin, DetailView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
        AppRole.EMPLOYEE,
    )

    model = Project

    context_object_name = "project"

    def get_template_names(self):

        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return ["admin/project_detail.html"]

        elif role == AppRole.MANAGER:
            return ["manager/project_detail.html"]

        return ["employee/project_detail.html"]

    def get_queryset(self):

        return get_visible_projects(
            self.request.user
        )

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        project = self.object

        tasks = Task.objects.filter(
            project=project
        ).select_related(
            "assigned_to"
        )
        role = current_user_role(self.request.user)

        if role == AppRole.EMPLOYEE:

            tasks = tasks.filter(
                assigned_to__user=self.request.user
            )

        total_tasks = tasks.count()

        completed_tasks = tasks.filter(
            status=Task.Status.COMPLETED
        ).count()

        in_progress_tasks = tasks.filter(
            status=Task.Status.IN_PROGRESS
        ).count()

        pending_tasks = tasks.filter(
            status=Task.Status.PENDING
        ).count()

        overdue_tasks = tasks.exclude(
            status=Task.Status.COMPLETED
        ).filter(
            deadline__lt=timezone.now().date()
        ).count()

        progress_percentage = (
            round(
                (completed_tasks / total_tasks) * 100,
                1
            )
            if total_tasks else 0
        )

        team_members = (
            project.team.employees.all()
            if project.team
            else Employee.objects.none()
        )

        member_stats = []

        for member in team_members:

            assigned_count = tasks.filter(
                assigned_to=member
            ).count()

            completed_count = tasks.filter(
                assigned_to=member,
                status=Task.Status.COMPLETED
            ).count()

            workload = workload_percentage(member)

            member_stats.append({

                "employee": member,

                "assigned": assigned_count,

                "completed": completed_count,

                "workload": workload,

            })

        ai_summary = project_ai_summary(project)

        team_health = (
            team_health_summary(project.team)
            if project.team
            else None
        )

        team_analytics_data = (
            team_analytics(project.team)
            if project.team
            else None
        )

        context.update({

            "tasks": tasks,

            "team_members": team_members,

            "total_tasks": total_tasks,

            "completed_tasks": completed_tasks,

            "in_progress_tasks": in_progress_tasks,

            "pending_tasks": pending_tasks,

            "overdue_tasks": overdue_tasks,

            "progress_percentage": progress_percentage,

            "member_stats": member_stats,

            "ai_summary": ai_summary,

            "team_health": team_health,

            "team_analytics": team_analytics_data,

        })

        return context



class PlanningHubView(RoleRequiredMixin, TemplateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/planning_hub.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tasks = get_visible_tasks(self.request.user)
        context.update(
            {
                'upcoming_tasks': tasks.exclude(status=Task.Status.COMPLETED).order_by('deadline')[:10],
                'upcoming_projects': get_visible_projects(self.request.user).exclude(status=Project.Status.COMPLETED)[:8],
                'today': timezone.localdate(),
            }
        )
        return context


class TaskListView(RoleRequiredMixin, ListView):

    allowed_roles = (
        AppRole.ADMIN,
        AppRole.MANAGER,
        AppRole.EMPLOYEE
    )

    context_object_name = 'tasks'

    def get_template_names(self):

        role = current_user_role(self.request.user)

        if role == AppRole.ADMIN:
            return ['admin/task_list.html']

        elif role == AppRole.MANAGER:
            return ['manager/task_list.html']

        return ['employee/task_list.html']

    def get_queryset(self):

        queryset = get_visible_tasks(self.request.user)

        project_id = self.request.GET.get('project')
        assignment = self.request.GET.get('assignment')
        status = self.request.GET.get('status')

        if project_id:
            queryset = queryset.filter(project_id=project_id)

        if assignment == 'assigned':
            queryset = queryset.filter(
                assigned_to__isnull=False
            )

        elif assignment == 'unassigned':
            queryset = queryset.filter(
                assigned_to__isnull=True
            )

        if status:
            queryset = queryset.filter(status=status)

        return queryset

    def get_context_data(self, **kwargs):

        context = super().get_context_data(**kwargs)

        context['projects'] = get_visible_projects(
            self.request.user
        )

        context['current_role'] = current_user_role(
            self.request.user
        )

        context['filters'] = {
            'project': self.request.GET.get('project', ''),
            'assignment': self.request.GET.get('assignment', ''),
            'status': self.request.GET.get('status', ''),
        }

        return context

class KanbanBoardView(TaskListView):
    template_name = 'core/kanban.html'


class CalendarView(TaskListView):
    template_name = 'core/calendar.html'


class TaskCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    model = Task
    form_class = TaskForm
    template_name = 'core/form.html'

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if current_user_role(self.request.user) == AppRole.MANAGER:
            form.fields['project'].queryset = get_visible_projects(self.request.user)
            form.fields['assigned_to'].queryset = get_visible_employees(self.request.user)
        return form

    def form_valid(self, form):
        task = form.save(commit=False)
        task.created_by = self.request.user
        task.save()
        sync_project_status(task.project)
        create_audit_log(self.request.user, 'created_task', 'Task', task.id, task.title)
        if task.assigned_to and task.assigned_to.user:
            create_notification(task.assigned_to.user, 'New task assigned', f'{task.title} has been assigned to you.')
        messages.success(self.request, 'Task created successfully.')
        return redirect('task-detail', pk=task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Task'
        return context


class TaskDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    model = Task
    def get_template_names(self):

        role = self.request.user.role

        if role == "ADMIN":
            return ["admin/task_detail.html"]

        elif role == "MANAGER":
            return ["manager/task_detail.html"]

        return ["employee/task_detail.html"]
    context_object_name = 'task'

    def get_queryset(self):
        return get_visible_tasks(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.object
        context.update(
            {
                'ai_result': upsert_ai_result(task),
                'delay_risk': predict_delay_risk(task),
                'skill_gap': skill_gap_analysis(task),
                'task_anomaly': task_anomaly_analysis(task),
                'project_ai': project_ai_summary(task.project),
                'employee_ai_profile': employee_ai_profile(task.assigned_to) if task.assigned_to else None,
                'allocation_suggestions': [] if task.assigned_to else recommend_employee_for_task(task),
                'comments': task.activities.filter(activity_type=Activity.Type.COMMENT),
                'attachments': task.attachments.all(),
                'logs': task.logs.select_related('employee')[:10],
                'approval_request': task.latest_approval_request,
            }
        )
        return context


class TaskUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    model = Task
    form_class = TaskUpdateForm
    template_name = 'core/form.html'

    def get_queryset(self):
        return get_visible_tasks(self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if current_user_role(self.request.user) == AppRole.MANAGER:
            form.fields['project'].queryset = get_visible_projects(self.request.user)
            form.fields['assigned_to'].queryset = get_visible_employees(self.request.user)
        return form

    def form_valid(self, form):
        task = form.save()
        sync_project_status(task.project)
        create_audit_log(self.request.user, 'updated_task', 'Task', task.id, task.title)
        messages.success(self.request, 'Task updated successfully.')
        return redirect('task-detail', pk=task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Task'
        return context


class TaskApprovalUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    model = Request
    form_class = TaskApprovalForm
    template_name = 'core/form.html'

    def get_object(self, queryset=None):
        task = get_object_or_404(get_visible_tasks(self.request.user), pk=self.kwargs['pk'])
        request_obj = task.latest_approval_request
        if request_obj is None:
            raise PermissionDenied('No approval request exists for this task.')
        return request_obj

    def form_valid(self, form):
        approval_request = form.save(commit=False)
        approval_request.reviewed_by = self.request.user
        approval_request.reviewed_at = timezone.now()
        approval_request.save()

        task = approval_request.task
        if approval_request.status == Request.Status.APPROVED:
            task.status = Task.Status.COMPLETED
            task.progress = 100
        else:
            task.status = Task.Status.IN_PROGRESS
        task.save()
        sync_project_status(task.project)

        if task.assigned_to and task.assigned_to.user:
            create_notification(task.assigned_to.user, 'Task approval updated', f'{task.title} approval status: {approval_request.get_status_display()}.')

        create_audit_log(self.request.user, 'reviewed_task_request', 'Request', approval_request.id, task.title)
        messages.success(self.request, 'Approval decision saved.')
        return redirect('task-detail', pk=task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Review Task Approval'
        return context


class TaskCommentCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    form_class = TaskCommentForm
    template_name = 'core/form.html'

    def dispatch(self, request, *args, **kwargs):
        self.task = get_object_or_404(get_visible_tasks(request.user), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        create_activity(
            Activity.Type.COMMENT,
            user=self.request.user,
            task=self.task,
            project=self.task.project,
            title=f'Comment on {self.task.title}',
            message=form.cleaned_data['message'],
        )
        messages.success(self.request, 'Comment added.')
        return redirect('task-detail', pk=self.task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Comment'
        return context


class TaskAttachmentCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    model = Attachment
    form_class = TaskAttachmentForm
    template_name = 'core/form.html'

    def dispatch(self, request, *args, **kwargs):
        self.task = get_object_or_404(get_visible_tasks(request.user), pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        attachment = form.save(commit=False)
        attachment.task = self.task
        attachment.uploaded_by = self.request.user
        attachment.save()
        messages.success(self.request, 'Attachment uploaded.')
        return redirect('task-detail', pk=self.task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Upload Attachment'
        return context


class TaskLogCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    model = TaskLog
    form_class = TaskLogForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('task-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['employee'] = get_employee_for_user(self.request.user)
        kwargs['role'] = current_user_role(self.request.user)
        task_id = self.request.GET.get('task')
        if task_id:
            try:
                task = get_visible_tasks(self.request.user).get(pk=task_id)
                kwargs.setdefault('initial', {})['task'] = task
                kwargs.setdefault('initial', {})['progress_after_log'] = task.progress
            except Task.DoesNotExist:
                pass
        return kwargs

    def form_valid(self, form):
        employee = form.cleaned_data['employee']
        task = form.cleaned_data['task']
        role = current_user_role(self.request.user)
        if role == AppRole.EMPLOYEE:

            if task.assigned_to_id != employee.id:
                raise PermissionDenied(
                    "You can only log work for your assigned tasks."
                )

        elif role == AppRole.MANAGER:

            if employee.team.manager != self.request.user:
                raise PermissionDenied(
                    "You cannot log work for employees outside your team."
                )

        task_log = form.save()
        progress = form.cleaned_data.get('progress_after_log')
        if progress is not None:
            task.progress = progress
            if progress >= 100:
                task.status = Task.Status.SUBMITTED if task.requires_approval else Task.Status.COMPLETED
            elif progress > 0:
                task.status = Task.Status.IN_PROGRESS
            task.save()
            sync_project_status(task.project)

        if task.requires_approval and task.status == Task.Status.SUBMITTED:
            existing_request = Request.objects.filter(
            request_type=Request.Type.TASK_APPROVAL,
            task=task,
            status=Request.Status.PENDING
        ).first()

        if not existing_request:

            Request.objects.create(
                request_type=Request.Type.TASK_APPROVAL,
                task=task,
                employee=employee,
                raised_by=self.request.user,
                status=Request.Status.PENDING,
                remarks='Submitted for manager approval.'
            )

            if task.project.team and task.project.team.manager:
                create_notification(
                    task.project.team.manager,
                    'Task submitted for approval',
                    f'{task.title} is ready for review.'
                )

        create_audit_log(self.request.user, 'logged_work', 'TaskLog', task_log.id, task.title)
        messages.success(self.request, 'Work log saved successfully.')
        return redirect('task-detail', pk=task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Log Work'
        task_id = self.request.GET.get('task')
        if task_id:
            context['selected_task'] = get_object_or_404(get_visible_tasks(self.request.user), pk=task_id)
        return context


class AnalyticsView(RoleRequiredMixin, TemplateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/analytics.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = current_user_role(self.request.user)
        tasks = get_visible_tasks(self.request.user)
        employees = list(get_visible_employees(self.request.user))

        if role == AppRole.EMPLOYEE:
            employee = get_employee_for_user(self.request.user)
            analytics_rows = [(employee, employee_metrics(employee))] if employee else []
            employee_profile_rows = [(employee, employee_ai_profile(employee))] if employee else []
        else:
            analytics_rows = [(employee, employee_metrics(employee)) for employee in employees]
            employee_profile_rows = [(employee, employee_ai_profile(employee)) for employee in employees]

        context.update(
            {
                'analytics_role': role,
                'task_count': tasks.count(),
                'completed_count': tasks.filter(status=Task.Status.COMPLETED).count(),
                'delayed_count': len([task for task in tasks if task.is_delayed]),
                'employee_metrics': analytics_rows,
                'ai_results': [upsert_ai_result(task) for task in tasks[:12]],
                'employee_ai_profiles': employee_profile_rows,
                'project_ai_rows': [project_ai_summary(project) for project in get_visible_projects(self.request.user)[:10]],
                'team_health_rows': [team_health_summary(team) for team in get_visible_teams(self.request.user)],
                'task_anomalies': [(task, task_anomaly_analysis(task)) for task in tasks[:10]],
            }
        )
        return context


class ApprovalQueueView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    template_name = 'core/approval_queue.html'
    context_object_name = 'requests'

    def get_queryset(self):
        return get_visible_requests(self.request.user).filter(
            request_type=Request.Type.TASK_APPROVAL,
            status=Request.Status.PENDING,
        )


class PerformanceReviewListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/performance_review_list.html'
    context_object_name = 'reviews'

    def get_queryset(self):
        queryset = get_visible_activities(self.request.user).filter(activity_type=Activity.Type.REVIEW)
        if current_user_role(self.request.user) == AppRole.EMPLOYEE:
            queryset = queryset.filter(user=self.request.user)
        return queryset


class PerformanceReviewCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    form_class = PerformanceReviewForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('performance-review-list')

    def form_valid(self, form):
        employee = form.cleaned_data['employee']
        create_activity(
            Activity.Type.REVIEW,
            user=employee.user,
            project=None,
            title=form.cleaned_data['title'] or f'Performance review - {employee.name}',
            message=form.cleaned_data['message'],
            rating=form.cleaned_data.get('rating'),
        )
        create_audit_log(self.request.user, 'created_review', 'Employee', employee.id, employee.name)
        messages.success(self.request, 'Performance review recorded.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Performance Review'
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['employee_queryset'] = get_visible_employees(self.request.user)
        return kwargs


class NotificationListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/notification_list.html'
    context_object_name = 'notifications'
    def get_queryset(self):
        return Activity.objects.filter(activity_type=Activity.Type.NOTIFICATION, user=self.request.user)


class LeaveRequestListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/leave_request_list.html'
    context_object_name = 'requests'

    def get_queryset(self):
        return get_visible_requests(self.request.user).filter(request_type=Request.Type.LEAVE)


class LeaveRequestCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    model = Request
    form_class = LeaveRequestForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('leave-request-list')

    def dispatch(self, request, *args, **kwargs):
        self.employee = get_employee_for_user(request.user)
        if self.employee is None:
            raise PermissionDenied('A linked employee profile is required to request leave.')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        leave_request = form.save(commit=False)
        leave_request.employee = self.employee
        leave_request.raised_by = self.request.user
        leave_request.request_type = Request.Type.LEAVE
        leave_request.status = Request.Status.PENDING
        leave_request.save()
        if self.employee.team and self.employee.team.manager:
            create_notification(self.employee.team.manager, 'New leave request', f'{self.employee.name} submitted a leave request.')
        messages.success(self.request, 'Leave request submitted.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Leave Request'
        return context


class LeaveApprovalUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    model = Request
    form_class = LeaveApprovalForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('leave-request-list')

    def get_queryset(self):
        return get_visible_requests(self.request.user).filter(request_type=Request.Type.LEAVE)

    def form_valid(self, form):
        leave_request = form.save(commit=False)
        leave_request.reviewed_by = self.request.user
        leave_request.reviewed_at = timezone.now()
        leave_request.save()
        if leave_request.employee.user:
            create_notification(
                leave_request.employee.user,
                'Leave request updated',
                f'Your leave request status is now {leave_request.get_status_display()}.',
            )
        messages.success(self.request, 'Leave request updated.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Review Leave Request'
        return context


class AuditLogListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN,)
    template_name = 'core/audit_log_list.html'
    context_object_name = 'activities'

    def get_queryset(self):
        return Activity.objects.filter(activity_type=Activity.Type.AUDIT)
