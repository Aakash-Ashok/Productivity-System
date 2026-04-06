from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        MANAGER = 'manager', 'Manager'
        EMPLOYEE = 'employee', 'Employee'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} ({self.role})'


class Team(models.Model):
    name = models.CharField(max_length=150, unique=True)
    manager = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_teams',
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Employee(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        BUSY = 'busy', 'Busy'
        OVERLOADED = 'overloaded', 'Overloaded'

    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employee_profile',
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
    )
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    job_title = models.CharField(max_length=120, blank=True)
    skills = models.TextField(help_text='Comma-separated skills for smart allocation.')
    experience_years = models.PositiveIntegerField(default=0)
    weekly_capacity_hours = models.PositiveIntegerField(default=40)
    availability = models.CharField(
        max_length=20,
        choices=Availability.choices,
        default=Availability.AVAILABLE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def skill_list(self):
        return [skill.strip().lower() for skill in self.skills.split(',') if skill.strip()]


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNING = 'planning', 'Planning'
        ACTIVE = 'active', 'Active'
        ON_HOLD = 'on_hold', 'On Hold'
        COMPLETED = 'completed', 'Completed'

    name = models.CharField(max_length=200)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='projects')
    description = models.TextField(blank=True)
    deadline = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['deadline', 'name']

    def __str__(self):
        return self.name

    @property
    def progress(self):
        tasks = self.tasks.all()
        if not tasks:
            return 0
        return round(sum(task.progress for task in tasks) / tasks.count(), 2)


class Milestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    deadline = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=Project.Status.choices,
        default=Project.Status.PLANNING,
    )

    class Meta:
        ordering = ['deadline', 'name']

    def __str__(self):
        return f'{self.project.name} - {self.name}'


class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
        CRITICAL = 'critical', 'Critical'

    class Status(models.TextChoices):
        TODO = 'todo', 'To Do'
        IN_PROGRESS = 'in_progress', 'In Progress'
        SUBMITTED = 'submitted', 'Submitted for Approval'
        REVIEW = 'review', 'In Review'
        COMPLETED = 'completed', 'Completed'
        BLOCKED = 'blocked', 'Blocked'

    class ApprovalStatus(models.TextChoices):
        NOT_REQUIRED = 'not_required', 'Not Required'
        PENDING = 'pending', 'Pending Approval'
        APPROVED = 'approved', 'Approved'
        REWORK = 'rework', 'Rework Requested'

    title = models.CharField(max_length=200)
    description = models.TextField()
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='tasks',
        null=True,
        blank=True,
    )
    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    assigned_to = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tasks',
    )
    required_skills = models.TextField(
        blank=True,
        help_text='Comma-separated skills needed for the task.',
    )
    deadline = models.DateField()
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TODO)
    difficulty = models.PositiveIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1 = simple, 5 = very complex',
    )
    requires_approval = models.BooleanField(default=False)
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.NOT_REQUIRED,
    )
    manager_remark = models.TextField(blank=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    progress = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['deadline', '-priority', 'title']

    def __str__(self):
        return self.title

    @property
    def required_skill_list(self):
        return [skill.strip().lower() for skill in self.required_skills.split(',') if skill.strip()]

    @property
    def is_delayed(self):
        today = timezone.localdate()
        return self.status != self.Status.COMPLETED and self.deadline < today


class TaskLog(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='logs')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='task_logs')
    hours_spent = models.DecimalField(max_digits=5, decimal_places=2)
    log_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    progress_after_log = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-log_date', '-created_at']

    def __str__(self):
        return f'{self.employee} - {self.task}'


class AIResult(models.Model):
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name='ai_result')
    predicted_time = models.FloatField(default=0)
    delay_risk = models.FloatField(default=0)
    suggestions = models.TextField(blank=True)
    recommended_action = models.TextField(blank=True)
    skill_gap = models.TextField(blank=True)
    learning_recommendations = models.TextField(blank=True)
    workload_signal = models.CharField(max_length=40, blank=True)
    burnout_score = models.FloatField(default=0)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f'AI result for {self.task}'


class PerformanceReview(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='performance_reviews')
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='performance_reviews_written')
    review_date = models.DateField(default=timezone.localdate)
    rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    remarks = models.TextField()

    class Meta:
        ordering = ['-review_date', '-id']

    def __str__(self):
        return f'Review for {self.employee} on {self.review_date}'


class Notification(models.Model):
    class Kind(models.TextChoices):
        TASK = 'task', 'Task'
        DEADLINE = 'deadline', 'Deadline'
        DELAY = 'delay', 'Delay'
        BURNOUT = 'burnout', 'Burnout'
        APPROVAL = 'approval', 'Approval'
        SYSTEM = 'system', 'System'

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=180)
    message = models.TextField()
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} -> {self.recipient.username}'


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='task_comments')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Comment on {self.task}'


class TaskAttachment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='task_attachments')
    file = models.FileField(upload_to='task_attachments/')
    label = models.CharField(max_length=180)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.label


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_requests')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    manager_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.employee} leave {self.start_date} to {self.end_date}'


class RecurringTask(models.Model):
    class Frequency(models.TextChoices):
        DAILY = 'daily', 'Daily'
        WEEKLY = 'weekly', 'Weekly'
        MONTHLY = 'monthly', 'Monthly'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='recurring_tasks')
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_tasks')
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.WEEKLY)
    next_due_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['next_due_date', 'title']

    def __str__(self):
        return self.title


class AuditLog(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    action = models.CharField(max_length=120)
    entity_type = models.CharField(max_length=80)
    entity_id = models.PositiveIntegerField()
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.action} on {self.entity_type}#{self.entity_id}'


class PerformanceMetric(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='performance_metric')
    tasks_completed = models.PositiveIntegerField(default=0)
    avg_completion_time = models.FloatField(default=0)
    delayed_tasks = models.PositiveIntegerField(default=0)
    active_tasks = models.PositiveIntegerField(default=0)
    average_daily_hours = models.FloatField(default=0)
    burnout_risk = models.CharField(max_length=20, default='Low')
    completion_rate = models.FloatField(default=0)
    last_calculated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee__name']

    def __str__(self):
        return f'Performance for {self.employee}'
