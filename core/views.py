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
    queryset = Employee.objects.select_related('user', 'team')
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
    queryset = Task.objects.select_related('project', 'project__team', 'assigned_to', 'assigned_to__user')
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
    queryset = Activity.objects.select_related('user', 'task', 'project')
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
    role_template_map = {
        AppRole.ADMIN: 'admin',
        AppRole.MANAGER: 'manager',
        AppRole.EMPLOYEE: 'employee',
    }

    def get_template_names(self):
        names = list(super().get_template_names())
        role_dir = self.role_template_map.get(current_user_role(self.request.user))
        if not role_dir:
            return names
        role_specific = []
        for name in names:
            if name.startswith('core/'):
                role_specific.append(f'{role_dir}/{name.split("/", 1)[1]}')
            else:
                role_specific.append(name)
        return role_specific or names


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
    template_name = 'core/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = current_user_role(self.request.user)
        visible_tasks = get_visible_tasks(self.request.user)
        visible_projects = get_visible_projects(self.request.user)
        visible_requests = get_visible_requests(self.request.user)
        notifications = get_visible_activities(self.request.user).filter(activity_type=Activity.Type.NOTIFICATION)[:6]

        context.update(
            {
                'dashboard_role': role,
                'notifications': notifications,
                'recent_tasks': visible_tasks[:6],
                'recent_requests': visible_requests[:6],
            }
        )

        if role == AppRole.ADMIN:
            context.update(dashboard_summary())
            context['teams'] = get_visible_teams(self.request.user)[:6]
        elif role == AppRole.MANAGER:
            employees = list(get_visible_employees(self.request.user))
            employee_rows = [(employee, employee_metrics(employee)) for employee in employees]
            managed_projects = list(visible_projects[:6])
            context.update(
                {
                    'managed_teams': get_visible_teams(self.request.user),
                    'managed_projects': managed_projects,
                    'team_members': employees,
                    'team_summary': employee_rows[:6],
                    'team_health_rows': [team_health_summary(team) for team in get_visible_teams(self.request.user)],
                    'managed_project_ai': [project_ai_summary(project) for project in managed_projects],
                    'pending_approvals': visible_requests.filter(
                        request_type=Request.Type.TASK_APPROVAL,
                        status=Request.Status.PENDING,
                    )[:6],
                    'team_leave_requests': visible_requests.filter(request_type=Request.Type.LEAVE)[:5],
                }
            )
        else:
            employee = get_employee_for_user(self.request.user)
            my_tasks = visible_tasks.order_by('deadline')
            context.update(
                {
                    'employee_record': employee,
                    'my_tasks': my_tasks,
                    'my_due_soon': my_tasks.exclude(status=Task.Status.COMPLETED)[:5],
                    'my_completed_tasks': my_tasks.filter(status=Task.Status.COMPLETED).count(),
                    'my_pending_tasks': my_tasks.exclude(status=Task.Status.COMPLETED).count(),
                    'my_logs': TaskLog.objects.filter(employee=employee).select_related('task')[:8] if employee else [],
                    'my_ai_results': [upsert_ai_result(task) for task in my_tasks[:5]],
                    'my_metric': employee_metrics(employee) if employee else None,
                    'my_ai_profile': employee_ai_profile(employee) if employee else None,
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
    template_name = 'core/user_list.html'
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
    allowed_roles = (AppRole.ADMIN,)
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


class TeamListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER)
    template_name = 'core/team_list.html'
    context_object_name = 'teams'

    def get_queryset(self):
        return get_visible_teams(self.request.user)


class TeamCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Team
    form_class = TeamForm
    template_name = 'core/form.html'
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
    template_name = 'core/form.html'
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


class EmployeeListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/employee_list.html'
    context_object_name = 'employees'

    def get_queryset(self):
        return get_visible_employees(self.request.user)


class EmployeeCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Employee
    form_class = EmployeeForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('employee-list')

    def form_valid(self, form):
        employee = form.save()
        if employee.created_user:
            send_credentials_email(employee.email, employee.created_user.username, employee.created_password, AppRole.EMPLOYEE)
        create_audit_log(self.request.user, 'created_employee', 'Employee', employee.id, employee.name)
        messages.success(self.request, 'Employee created successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Employee'
        return context


class EmployeeUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Employee
    form_class = EmployeeForm
    template_name = 'core/form.html'
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


class ProjectListView(RoleRequiredMixin, ListView):
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/project_list.html'
    context_object_name = 'projects'

    def get_queryset(self):
        return get_visible_projects(self.request.user)


class ProjectCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Project
    form_class = ProjectForm
    template_name = 'core/form.html'
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


class ProjectUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = (AppRole.ADMIN,)
    model = Project
    form_class = ProjectForm
    template_name = 'core/form.html'
    success_url = reverse_lazy('project-list')

    def form_valid(self, form):
        project = form.save()
        create_audit_log(self.request.user, 'updated_project', 'Project', project.id, project.name)
        messages.success(self.request, 'Project updated successfully.')
        return redirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Edit Project'
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
    allowed_roles = (AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE)
    template_name = 'core/task_list.html'
    context_object_name = 'tasks'

    def get_queryset(self):
        queryset = get_visible_tasks(self.request.user)
        project_id = self.request.GET.get('project')
        assignment = self.request.GET.get('assignment')
        status = self.request.GET.get('status')

        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if assignment == 'assigned':
            queryset = queryset.filter(assigned_to__isnull=False)
        elif assignment == 'unassigned':
            queryset = queryset.filter(assigned_to__isnull=True)
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['projects'] = get_visible_projects(self.request.user)
        context['current_role'] = current_user_role(self.request.user)
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
    template_name = 'core/task_detail.html'
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
        if role == AppRole.EMPLOYEE and task.assigned_to_id != employee.id:
            raise PermissionDenied('You can only log work for your own assigned tasks.')

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
            approval_request, created = Request.objects.get_or_create(
                request_type=Request.Type.TASK_APPROVAL,
                task=task,
                employee=employee,
                status=Request.Status.PENDING,
                defaults={'raised_by': self.request.user, 'remarks': 'Submitted for manager approval.'},
            )
            if created and task.project.team and task.project.team.manager:
                create_notification(task.project.team.manager, 'Task submitted for approval', f'{task.title} is ready for review.')

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
        return get_visible_activities(self.request.user).filter(activity_type=Activity.Type.NOTIFICATION, user=self.request.user)


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
