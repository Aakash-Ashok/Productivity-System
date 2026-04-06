from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from .forms import (
    EmployeeForm,
    LeaveApprovalForm,
    LeaveRequestForm,
    MilestoneForm,
    PerformanceReviewForm,
    ProfileForm,
    ProjectForm,
    RecurringTaskForm,
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
from .models import (
    AuditLog,
    Employee,
    LeaveRequest,
    Milestone,
    Notification,
    PerformanceMetric,
    PerformanceReview,
    Project,
    RecurringTask,
    Task,
    TaskAttachment,
    TaskComment,
    TaskLog,
    Team,
    UserProfile,
)
from .services import (
    create_audit_log,
    create_notification,
    dashboard_summary,
    predict_delay_risk,
    predict_task_completion_hours,
    productivity_trend,
    recommend_employee_for_task,
    refresh_performance_metrics,
    send_credentials_email,
    skill_gap_analysis,
    sync_project_status,
    upsert_ai_result,
)


def current_user_role(user):
    profile = getattr(user, 'profile', None)
    if user.is_superuser:
        return UserProfile.Role.ADMIN
    return getattr(profile, 'role', None)


class RoleTemplateMixin:
    role_template_map = {
        UserProfile.Role.ADMIN: 'admin',
        UserProfile.Role.MANAGER: 'manager',
        UserProfile.Role.EMPLOYEE: 'employee',
    }

    def get_template_names(self):
        names = list(super().get_template_names())
        role = current_user_role(self.request.user)
        role_dir = self.role_template_map.get(role)
        if not role_dir:
            return names
        role_specific = []
        for name in names:
            if name.startswith('core/'):
                role_specific.append(f'{role_dir}/{name.split("/", 1)[1]}')
        return role_specific or names


class RoleRequiredMixin(RoleTemplateMixin, LoginRequiredMixin):
    allowed_roles: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)
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


def _sync_task_completion(task: Task) -> None:
    if task.status == Task.Status.COMPLETED and task.completed_at is None:
        task.completed_at = timezone.now()
    elif task.status != Task.Status.COMPLETED:
        task.completed_at = None


class DashboardView(RoleLoginRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', 'unassigned')
        context['dashboard_role'] = role
        context['recent_logs'] = TaskLog.objects.select_related('task', 'employee')[:8]
        context['notifications'] = Notification.objects.filter(recipient=self.request.user)[:6]

        if role == UserProfile.Role.ADMIN:
            context.update(dashboard_summary())
            context['approval_queue'] = Task.objects.filter(approval_status=Task.ApprovalStatus.PENDING).select_related('project', 'assigned_to')[:6]
        elif role == UserProfile.Role.MANAGER:
            managed_teams = Team.objects.filter(manager=self.request.user)
            managed_projects = Project.objects.filter(team__manager=self.request.user).select_related('team')
            managed_tasks = Task.objects.filter(project__team__manager=self.request.user).select_related('project', 'assigned_to')
            risk_rows = []
            for task in managed_tasks.exclude(status=Task.Status.COMPLETED):
                risk = predict_delay_risk(task)
                if risk['label'] in {'High', 'Medium'}:
                    risk_rows.append((task, risk))

            context.update(
                {
                    'managed_teams': managed_teams,
                    'managed_projects': managed_projects,
                    'team_members': Employee.objects.filter(team__manager=self.request.user).select_related('team'),
                    'managed_tasks': managed_tasks[:8],
                    'pending_approvals': managed_tasks.filter(approval_status=Task.ApprovalStatus.PENDING)[:6],
                    'team_leave_requests': LeaveRequest.objects.filter(employee__team__manager=self.request.user)[:5],
                    'manager_risks': risk_rows[:5],
                    'burnout_alerts': PerformanceMetric.objects.select_related('employee').filter(
                        employee__team__manager=self.request.user
                    ).exclude(burnout_risk='Low'),
                }
            )
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            if employee:
                my_tasks = Task.objects.filter(assigned_to=employee).select_related('project').order_by('deadline')
                context.update(
                    {
                        'employee_record': employee,
                        'my_tasks': my_tasks,
                        'my_due_soon': my_tasks.exclude(status=Task.Status.COMPLETED)[:5],
                        'my_completed_tasks': my_tasks.filter(status=Task.Status.COMPLETED).count(),
                        'my_pending_tasks': my_tasks.exclude(status=Task.Status.COMPLETED).count(),
                        'my_logs': TaskLog.objects.filter(employee=employee).select_related('task')[:8],
                        'my_ai_results': [upsert_ai_result(task) for task in my_tasks[:5]],
                        'my_metric': getattr(employee, 'performance_metric', None),
                    }
                )
            else:
                context['employee_record'] = None
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
        context['profile_employee'] = getattr(self.request.user, 'employee_profile', None)
        return context


class UserListView(RoleRequiredMixin, ListView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = User
    template_name = 'core/user_list.html'
    context_object_name = 'users'

    def get_queryset(self):
        return User.objects.select_related('profile').order_by('username')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user_rows'] = [
            {
                'id': account.id,
                'username': account.username,
                'email': account.email,
                'role': getattr(getattr(account, 'profile', None), 'role', 'unassigned'),
                'is_staff': account.is_staff,
                'is_active': account.is_active,
            }
            for account in context['users']
        ]
        return context


class UserCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = User
    form_class = UserAccountForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('user-list')

    def form_valid(self, form):
        user = form.save()
        send_credentials_email(user.email, user.username, user.generated_password, user.generated_role)
        create_audit_log(self.request.user, 'created_user', 'User', user.id, user.username)
        messages.success(self.request, 'User account created successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create User'
        return context


class UserAdminUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = User
    form_class = UserAdminEditForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('user-list')

    def form_valid(self, form):
        user = form.save()
        create_audit_log(self.request.user, 'updated_user', 'User', user.id, user.username)
        messages.success(self.request, 'User updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Admin Actions: Edit User'
        return context


class TeamListView(RoleLoginRequiredMixin, ListView):
    model = Team
    template_name = 'core/team_list.html'
    context_object_name = 'teams'


class TeamCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = Team
    form_class = TeamForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('team-list')

    def form_valid(self, form):
        messages.success(self.request, 'Team created successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Team'
        return context


class TeamUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = Team
    form_class = TeamForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('team-list')

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(self.request.user, 'updated_team', 'Team', self.object.id, self.object.name)
        messages.success(self.request, 'Team updated successfully.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Admin Actions: Edit Team'
        return context


class EmployeeListView(RoleLoginRequiredMixin, ListView):
    model = Employee
    template_name = 'core/employee_list.html'
    context_object_name = 'employees'

    def get_queryset(self):
        queryset = Employee.objects.select_related('team', 'user').order_by('name')
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        if role == UserProfile.Role.MANAGER:
            queryset = queryset.filter(team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            queryset = queryset.filter(pk=employee.pk) if employee else queryset.none()
        return queryset


class EmployeeCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = Employee
    form_class = EmployeeForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('employee-list')

    def form_valid(self, form):
        employee = form.save()
        if employee.user:
            UserProfile.objects.update_or_create(user=employee.user, defaults={'role': UserProfile.Role.EMPLOYEE})
        if getattr(employee, 'created_user', None):
            send_credentials_email(employee.email, employee.created_user.username, employee.created_password, 'employee')
        create_audit_log(self.request.user, 'created_employee', 'Employee', employee.id, employee.name)
        messages.success(self.request, 'Employee added successfully.')
        refresh_performance_metrics()
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Employee'
        return context


class EmployeeUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = Employee
    form_class = EmployeeForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('employee-list')

    def form_valid(self, form):
        employee = form.save()
        create_audit_log(self.request.user, 'updated_employee', 'Employee', employee.id, employee.name)
        messages.success(self.request, 'Employee updated successfully.')
        refresh_performance_metrics()
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Admin Actions: Edit Employee'
        return context


class ProjectListView(RoleLoginRequiredMixin, ListView):
    model = Project
    template_name = 'core/project_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        return Project.objects.select_related('team').order_by('deadline', 'name')


class ProjectCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = Project
    form_class = ProjectForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('project-list')

    def form_valid(self, form):
        messages.success(self.request, 'Project created successfully.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Project'
        return context


class ProjectUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = Project
    form_class = ProjectForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('project-list')

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(self.request.user, 'updated_project', 'Project', self.object.id, self.object.name)
        messages.success(self.request, 'Project updated successfully.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Admin Actions: Edit Project'
        return context


class MilestoneListView(RoleLoginRequiredMixin, ListView):
    model = Milestone
    template_name = 'core/milestone_list.html'
    context_object_name = 'milestones'

    def get_queryset(self):
        queryset = Milestone.objects.select_related('project', 'project__team').order_by('deadline', 'name')
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        if role == UserProfile.Role.MANAGER:
            queryset = queryset.filter(project__team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            queryset = queryset.filter(tasks__assigned_to=employee).distinct() if employee else queryset.none()
        return queryset


class PlanningHubView(RoleLoginRequiredMixin, TemplateView):
    template_name = 'core/planning_hub.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)

        projects = Project.objects.select_related('team').order_by('deadline', 'name')
        milestones = Milestone.objects.select_related('project', 'project__team').order_by('deadline')
        recurring_tasks = RecurringTask.objects.select_related('project', 'assigned_to').order_by('next_due_date')
        calendar_items = Task.objects.select_related('project', 'assigned_to').order_by('deadline')

        if role == UserProfile.Role.MANAGER:
            projects = projects.filter(team__manager=self.request.user)
            milestones = milestones.filter(project__team__manager=self.request.user)
            recurring_tasks = recurring_tasks.filter(project__team__manager=self.request.user)
            calendar_items = calendar_items.filter(project__team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            projects = projects.filter(tasks__assigned_to=employee).distinct() if employee else projects.none()
            milestones = milestones.filter(tasks__assigned_to=employee).distinct() if employee else milestones.none()
            recurring_tasks = recurring_tasks.filter(assigned_to=employee) if employee else recurring_tasks.none()
            calendar_items = calendar_items.filter(assigned_to=employee) if employee else calendar_items.none()

        context.update(
            {
                'planning_role': role,
                'planning_projects': projects[:6],
                'planning_milestones': milestones[:6],
                'planning_recurring_tasks': recurring_tasks[:6],
                'planning_calendar_items': calendar_items[:8],
            }
        )
        return context


class MilestoneCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    model = Milestone
    form_class = MilestoneForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('milestone-list')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser:
            form.fields['project'].queryset = Project.objects.filter(team__manager=self.request.user)
        return form

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(self.request.user, 'created_milestone', 'Milestone', self.object.id, self.object.name)
        messages.success(self.request, 'Milestone created successfully.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Milestone'
        return context


class RecurringTaskListView(RoleLoginRequiredMixin, ListView):
    model = RecurringTask
    template_name = 'core/recurring_task_list.html'
    context_object_name = 'recurring_tasks'

    def get_queryset(self):
        queryset = RecurringTask.objects.select_related('project', 'assigned_to', 'project__team')
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        if role == UserProfile.Role.MANAGER:
            queryset = queryset.filter(project__team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            queryset = queryset.filter(assigned_to=employee) if employee else queryset.none()
        return queryset


class RecurringTaskCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    model = RecurringTask
    form_class = RecurringTaskForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('recurring-task-list')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser:
            form.fields['project'].queryset = Project.objects.filter(team__manager=self.request.user)
            form.fields['assigned_to'].queryset = Employee.objects.filter(team__manager=self.request.user)
        return form

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(self.request.user, 'created_recurring_task', 'RecurringTask', self.object.id, self.object.title)
        messages.success(self.request, 'Recurring task saved successfully.')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Recurring Task'
        return context


class TaskListView(RoleLoginRequiredMixin, ListView):
    model = Task
    template_name = 'core/task_list.html'
    context_object_name = 'tasks'

    def get_queryset(self):
        queryset = Task.objects.select_related('project', 'assigned_to', 'project__team').order_by('deadline', 'title')
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        project_id = self.request.GET.get('project')
        assignment = self.request.GET.get('assignment')

        if role == UserProfile.Role.MANAGER:
            queryset = queryset.filter(project__team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            queryset = queryset.filter(assigned_to=employee) if employee else queryset.none()

        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if assignment == 'assigned':
            queryset = queryset.filter(assigned_to__isnull=False)
        elif assignment == 'unassigned':
            queryset = queryset.filter(assigned_to__isnull=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        context['task_scope_label'] = (
            'All Tasks' if role == UserProfile.Role.ADMIN else
            'Team Tasks' if role == UserProfile.Role.MANAGER else
            'My Tasks'
        )
        project_queryset = Project.objects.select_related('team').order_by('name')
        if role == UserProfile.Role.MANAGER:
            project_queryset = project_queryset.filter(team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            project_queryset = project_queryset.filter(tasks__assigned_to=employee).distinct() if employee else project_queryset.none()
        context['project_filters'] = project_queryset
        context['selected_project'] = self.request.GET.get('project', '')
        context['selected_assignment'] = self.request.GET.get('assignment', '')
        return context


class ApprovalQueueView(RoleRequiredMixin, TemplateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    template_name = 'core/approval_queue.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tasks = Task.objects.select_related('project', 'assigned_to', 'project__team').filter(
            approval_status=Task.ApprovalStatus.PENDING
        )
        leave_requests = LeaveRequest.objects.select_related('employee', 'employee__team').filter(
            status=LeaveRequest.Status.PENDING
        )
        if not self.request.user.is_superuser:
            tasks = tasks.filter(project__team__manager=self.request.user)
            leave_requests = leave_requests.filter(employee__team__manager=self.request.user)
        context['approval_tasks'] = tasks
        context['leave_requests'] = leave_requests
        return context


class TaskCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    model = Task
    form_class = TaskForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('task-list')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser:
            form.fields['project'].queryset = Project.objects.filter(team__manager=self.request.user)
            form.fields['milestone'].queryset = Milestone.objects.filter(project__team__manager=self.request.user)
            form.fields['assigned_to'].queryset = Employee.objects.filter(team__manager=self.request.user)
        return form

    def form_valid(self, form):
        task = form.save(commit=False)
        task.created_by = self.request.user
        if task.requires_approval and task.status == Task.Status.COMPLETED:
            task.status = Task.Status.SUBMITTED
            task.approval_status = Task.ApprovalStatus.PENDING
        _sync_task_completion(task)
        task.save()
        sync_project_status(task.project)
        upsert_ai_result(task)
        create_audit_log(self.request.user, 'created_task', 'Task', task.id, task.title)
        if task.assigned_to and task.assigned_to.user:
            create_notification(
                task.assigned_to.user,
                'New task assigned',
                f'You have been assigned "{task.title}". Deadline: {task.deadline}.',
                Notification.Kind.TASK,
            )
        messages.success(self.request, 'Task created successfully.')
        refresh_performance_metrics()
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Task'
        return context


class TaskUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER, UserProfile.Role.EMPLOYEE)
    model = Task
    form_class = TaskUpdateForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('task-list')

    def dispatch(self, request, *args, **kwargs):
        task = self.get_object()
        profile = getattr(request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if request.user.is_superuser else getattr(profile, 'role', None)
        employee = getattr(request.user, 'employee_profile', None)

        if role == UserProfile.Role.EMPLOYEE and task.assigned_to != employee:
            raise PermissionDenied('You can only update your own assigned tasks.')
        if role == UserProfile.Role.MANAGER and task.project and task.project.team.manager != request.user:
            raise PermissionDenied('You can only update tasks from your team.')
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser:
            form.fields['project'].queryset = Project.objects.filter(team__manager=self.request.user)
            form.fields['milestone'].queryset = Milestone.objects.filter(project__team__manager=self.request.user)
            form.fields['assigned_to'].queryset = Employee.objects.filter(team__manager=self.request.user)
        return form

    def form_valid(self, form):
        previous_project = self.get_object().project
        task = form.save(commit=False)
        if task.requires_approval and task.status == Task.Status.COMPLETED:
            task.status = Task.Status.SUBMITTED
            task.approval_status = Task.ApprovalStatus.PENDING
        _sync_task_completion(task)
        task.save()
        sync_project_status(previous_project)
        sync_project_status(task.project)
        upsert_ai_result(task)
        create_audit_log(self.request.user, 'updated_task', 'Task', task.id, task.title)
        messages.success(self.request, 'Task updated successfully.')
        refresh_performance_metrics()
        return redirect('task-detail', pk=task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Update Task'
        return context


class TaskDetailView(RoleLoginRequiredMixin, DetailView):
    model = Task
    template_name = 'core/task_detail.html'
    context_object_name = 'task'

    def dispatch(self, request, *args, **kwargs):
        task = self.get_object()
        profile = getattr(request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if request.user.is_superuser else getattr(profile, 'role', None)
        employee = getattr(request.user, 'employee_profile', None)

        if role == UserProfile.Role.EMPLOYEE and task.assigned_to != employee:
            raise PermissionDenied('You can only view your own assigned tasks.')
        if role == UserProfile.Role.MANAGER and task.project and task.project.team.manager != request.user:
            raise PermissionDenied('You can only view tasks from your team.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.object
        viewer_employee = getattr(self.request.user, 'employee_profile', None)
        ai_result = upsert_ai_result(task)
        skill_gap = skill_gap_analysis(task)
        context['total_logged_hours'] = task.logs.aggregate(total=Sum('hours_spent'))['total'] or 0
        context['predicted_completion_hours'] = predict_task_completion_hours(task)
        context['delay_risk'] = predict_delay_risk(task)
        context['allocation_suggestions'] = recommend_employee_for_task(task)
        context['ai_result'] = ai_result
        context['can_log_task'] = bool(viewer_employee and task.assigned_to_id == viewer_employee.id)
        context['can_approve_task'] = bool(
            task.project
            and task.project.team
            and task.project.team.manager_id == self.request.user.id
            and task.approval_status == Task.ApprovalStatus.PENDING
        )
        context['comment_form'] = TaskCommentForm()
        context['attachment_form'] = TaskAttachmentForm()
        context['skill_gap'] = skill_gap
        context['matched_skills'] = skill_gap['matched_skills']
        context['missing_skills'] = skill_gap['missing_skills']
        context['learning_recommendations'] = skill_gap['learning_recommendations']
        context['comments'] = task.comments.select_related('author')
        context['attachments'] = task.attachments.select_related('uploaded_by')
        return context


class TaskCommentCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        form = TaskCommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.task = task
            comment.author = request.user
            comment.save()
            create_audit_log(request.user, 'commented_task', 'Task', task.id, comment.body[:120])
            messages.success(request, 'Comment added successfully.')
        return redirect('task-detail', pk=pk)


class TaskAttachmentCreateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        form = TaskAttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            attachment = form.save(commit=False)
            attachment.task = task
            attachment.uploaded_by = request.user
            attachment.save()
            create_audit_log(request.user, 'attached_file', 'Task', task.id, attachment.label)
            messages.success(request, 'Attachment uploaded successfully.')
        return redirect('task-detail', pk=pk)


class TaskLogCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER, UserProfile.Role.EMPLOYEE)
    model = TaskLog
    form_class = TaskLogForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('dashboard')

    def _current_employee(self):
        return getattr(self.request.user, 'employee_profile', None)

    def dispatch(self, request, *args, **kwargs):
        if self._current_employee() is None:
            raise PermissionDenied('Only users with their own assigned employee profile can log work.')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        employee = self._current_employee()
        kwargs['role'] = role
        kwargs['employee'] = employee
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        task_id = self.request.GET.get('task')
        employee = self._current_employee()

        if task_id:
            task = get_object_or_404(Task, pk=task_id)
            if employee is None or task.assigned_to != employee:
                raise PermissionDenied('You can only log work for your own assigned tasks.')
            initial['task'] = task
            initial['employee'] = employee
            initial['progress_after_log'] = task.progress
        elif employee:
            initial['employee'] = employee
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_task = None
        task_id = self.request.GET.get('task')
        if task_id:
            selected_task = get_object_or_404(Task, pk=task_id)
        elif self.request.method == 'GET':
            form_task = context['form'].initial.get('task')
            selected_task = form_task if isinstance(form_task, Task) else None
        context['title'] = 'Log Work'
        context['selected_task'] = selected_task
        context['current_progress'] = selected_task.progress if selected_task else None
        return context

    def form_valid(self, form):
        employee = self._current_employee()
        if employee is None:
            raise PermissionDenied('Only the assigned employee can log work for a task.')
        form.instance.employee = employee
        if form.instance.task.assigned_to != employee:
            raise PermissionDenied('You can only log work for your own assigned tasks.')
        response = super().form_valid(form)
        task = form.instance.task
        task.progress = form.instance.progress_after_log
        if task.progress >= 100:
            if task.requires_approval:
                task.status = Task.Status.SUBMITTED
                task.approval_status = Task.ApprovalStatus.PENDING
                if task.project and task.project.team and task.project.team.manager:
                    create_notification(
                        task.project.team.manager,
                        'Task submitted for approval',
                        f'"{task.title}" was submitted by {employee.name}.',
                        Notification.Kind.APPROVAL,
                    )
            else:
                task.status = Task.Status.COMPLETED
                task.approval_status = Task.ApprovalStatus.APPROVED
        elif task.progress > 0 and task.status == Task.Status.TODO:
            task.status = Task.Status.IN_PROGRESS
        _sync_task_completion(task)
        task.save()
        sync_project_status(task.project)
        upsert_ai_result(task)
        refresh_performance_metrics()
        messages.success(self.request, 'Work log recorded successfully.')
        return response


class AnalyticsView(RoleLoginRequiredMixin, TemplateView):
    template_name = 'core/analytics.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        context['analytics_role'] = role

        if role == UserProfile.Role.ADMIN:
            summary = dashboard_summary()
            context.update(summary)
            context['metrics'] = PerformanceMetric.objects.select_related('employee', 'employee__team')
            task_queryset = Task.objects.select_related('project', 'assigned_to').exclude(status=Task.Status.COMPLETED)
        elif role == UserProfile.Role.MANAGER:
            context['metrics'] = PerformanceMetric.objects.select_related('employee', 'employee__team').filter(
                employee__team__manager=self.request.user
            )
            context['team_summary'] = [
                item for item in dashboard_summary()['team_summary']
                if item['team'].manager_id == self.request.user.id
            ]
            context['project_summary'] = [
                item for item in dashboard_summary()['project_summary']
                if item['project'].team.manager_id == self.request.user.id
            ]
            task_queryset = Task.objects.select_related('project', 'assigned_to').filter(
                project__team__manager=self.request.user
            ).exclude(status=Task.Status.COMPLETED)
        else:
            employee = getattr(self.request.user, 'employee_profile', None)
            context['metrics'] = PerformanceMetric.objects.select_related('employee', 'employee__team').filter(
                employee=employee
            ) if employee else PerformanceMetric.objects.none()
            context['team_summary'] = []
            context['project_summary'] = []
            task_queryset = Task.objects.select_related('project', 'assigned_to').filter(
                assigned_to=employee
            ).exclude(status=Task.Status.COMPLETED) if employee else Task.objects.none()

        context['projects'] = Project.objects.select_related('team')
        context['high_risk_tasks'] = []
        for task in task_queryset:
            risk = predict_delay_risk(task)
            if risk['label'] in {'High', 'Medium'}:
                context['high_risk_tasks'].append((task, risk))
        context['trend_data'] = productivity_trend()
        return context


class KanbanBoardView(RoleLoginRequiredMixin, TemplateView):
    template_name = 'core/kanban.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tasks = TaskListView()
        tasks.request = self.request
        queryset = tasks.get_queryset()
        columns = [
            (Task.Status.TODO, 'To Do'),
            (Task.Status.IN_PROGRESS, 'In Progress'),
            (Task.Status.SUBMITTED, 'Submitted'),
            (Task.Status.REVIEW, 'Review'),
            (Task.Status.COMPLETED, 'Completed'),
            (Task.Status.BLOCKED, 'Blocked'),
        ]
        context['kanban_columns'] = [
            {'status': status, 'label': label, 'tasks': queryset.filter(status=status)}
            for status, label in columns
        ]
        return context


class CalendarView(RoleLoginRequiredMixin, TemplateView):
    template_name = 'core/calendar.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tasks = TaskListView()
        tasks.request = self.request
        queryset = tasks.get_queryset()
        context['calendar_items'] = queryset.order_by('deadline')[:30]
        return context


class UserToggleActiveView(RoleRequiredMixin, View):
    allowed_roles = (UserProfile.Role.ADMIN,)

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user == request.user:
            messages.error(request, 'You cannot deactivate your own account here.')
            return redirect('user-list')
        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        create_audit_log(request.user, 'toggled_user_active', 'User', user.id, f'is_active={user.is_active}')
        messages.success(request, f'{user.username} is now {"active" if user.is_active else "inactive"}.')
        return redirect('user-list')


class TaskApprovalUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    model = Task
    form_class = TaskApprovalForm
    template_name = 'core/form.html'

    def dispatch(self, request, *args, **kwargs):
        task = self.get_object()
        if not request.user.is_superuser and (not task.project or task.project.team.manager_id != request.user.id):
            raise PermissionDenied('You can only approve tasks from your team.')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        task = form.save(commit=False)
        if task.approval_status == Task.ApprovalStatus.APPROVED:
            task.status = Task.Status.COMPLETED
        elif task.approval_status == Task.ApprovalStatus.REWORK:
            task.status = Task.Status.IN_PROGRESS
        task.save()
        sync_project_status(task.project)
        create_audit_log(self.request.user, 'reviewed_task_approval', 'Task', task.id, task.approval_status)
        if task.assigned_to and task.assigned_to.user:
            create_notification(
                task.assigned_to.user,
                'Task approval updated',
                f'"{task.title}" is now {task.get_approval_status_display()}.',
                Notification.Kind.APPROVAL,
            )
        messages.success(self.request, 'Task approval updated successfully.')
        return redirect('task-detail', pk=task.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Approve Task'
        return context


class PerformanceReviewListView(RoleRequiredMixin, ListView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER, UserProfile.Role.EMPLOYEE)
    model = PerformanceReview
    template_name = 'core/performance_review_list.html'
    context_object_name = 'reviews'

    def get_queryset(self):
        queryset = PerformanceReview.objects.select_related('employee', 'manager', 'employee__team')
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        if role == UserProfile.Role.MANAGER:
            queryset = queryset.filter(employee__team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            queryset = queryset.filter(employee=employee) if employee else queryset.none()
        return queryset


class PerformanceReviewCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    model = PerformanceReview
    form_class = PerformanceReviewForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('performance-review-list')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_superuser:
            form.fields['employee'].queryset = Employee.objects.filter(team__manager=self.request.user)
        return form

    def form_valid(self, form):
        review = form.save(commit=False)
        review.manager = self.request.user
        review.save()
        create_audit_log(self.request.user, 'created_performance_review', 'PerformanceReview', review.id, review.employee.name)
        if review.employee.user:
            create_notification(
                review.employee.user,
                'Performance review added',
                f'A new performance review with rating {review.rating}/5 has been added.',
                Notification.Kind.SYSTEM,
            )
        messages.success(self.request, 'Performance review saved successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Add Performance Review'
        return context


class NotificationListView(RoleLoginRequiredMixin, ListView):
    model = Notification
    template_name = 'core/notification_list.html'
    context_object_name = 'notifications'

    def get_queryset(self):
        queryset = Notification.objects.filter(recipient=self.request.user)
        queryset.filter(is_read=False).update(is_read=True)
        return queryset


class LeaveRequestListView(RoleRequiredMixin, ListView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER, UserProfile.Role.EMPLOYEE)
    model = LeaveRequest
    template_name = 'core/leave_request_list.html'
    context_object_name = 'leave_requests'

    def get_queryset(self):
        queryset = LeaveRequest.objects.select_related('employee', 'employee__team')
        profile = getattr(self.request.user, 'profile', None)
        role = UserProfile.Role.ADMIN if self.request.user.is_superuser else getattr(profile, 'role', None)
        if role == UserProfile.Role.MANAGER:
            queryset = queryset.filter(employee__team__manager=self.request.user)
        elif role == UserProfile.Role.EMPLOYEE:
            employee = getattr(self.request.user, 'employee_profile', None)
            queryset = queryset.filter(employee=employee) if employee else queryset.none()
        return queryset


class LeaveRequestCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER, UserProfile.Role.EMPLOYEE)
    model = LeaveRequest
    form_class = LeaveRequestForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('leave-request-list')

    def dispatch(self, request, *args, **kwargs):
        if getattr(request.user, 'employee_profile', None) is None:
            raise PermissionDenied('A linked employee profile is required to request leave.')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        leave = form.save(commit=False)
        leave.employee = self.request.user.employee_profile
        leave.save()
        if leave.employee.team and leave.employee.team.manager:
            create_notification(
                leave.employee.team.manager,
                'Leave request submitted',
                f'{leave.employee.name} requested leave from {leave.start_date} to {leave.end_date}.',
                Notification.Kind.SYSTEM,
            )
        create_audit_log(self.request.user, 'created_leave_request', 'LeaveRequest', leave.id, leave.reason[:120])
        messages.success(self.request, 'Leave request submitted successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Request Leave'
        return context


class LeaveApprovalUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (UserProfile.Role.ADMIN, UserProfile.Role.MANAGER)
    model = LeaveRequest
    form_class = LeaveApprovalForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('leave-request-list')

    def dispatch(self, request, *args, **kwargs):
        leave = self.get_object()
        if not request.user.is_superuser and leave.employee.team.manager_id != request.user.id:
            raise PermissionDenied('You can only review leave requests from your team.')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        leave = form.save()
        if leave.employee.user:
            create_notification(
                leave.employee.user,
                'Leave request updated',
                f'Your leave request is now {leave.get_status_display()}.',
                Notification.Kind.SYSTEM,
            )
        create_audit_log(self.request.user, 'updated_leave_request', 'LeaveRequest', leave.id, leave.status)
        messages.success(self.request, 'Leave request updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Review Leave Request'
        return context


class AuditLogListView(RoleRequiredMixin, ListView):
    allowed_roles = (UserProfile.Role.ADMIN,)
    model = AuditLog
    template_name = 'core/audit_log_list.html'
    context_object_name = 'audit_logs'
