from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class AppRole(models.TextChoices):
    ADMIN = 'admin', 'Admin'
    MANAGER = 'manager', 'Manager'
    EMPLOYEE = 'employee', 'Employee'


def assign_user_role(user: User, role: str) -> User:
    user.groups.clear()

    if role == AppRole.ADMIN:
        user.is_staff = True
        user.is_superuser = True
    elif role == AppRole.MANAGER:
        user.is_staff = True
        user.is_superuser = False
        group, _ = Group.objects.get_or_create(name=AppRole.MANAGER)
        user.groups.add(group)
    else:
        user.is_staff = False
        user.is_superuser = False
        group, _ = Group.objects.get_or_create(name=AppRole.EMPLOYEE)
        user.groups.add(group)

    user.save(update_fields=['is_staff', 'is_superuser'])
    return user


def current_user_role(user: User | None) -> str:
    if not user or not user.is_authenticated:
        return 'guest'
    if user.is_superuser:
        return AppRole.ADMIN
    if user.groups.filter(name=AppRole.MANAGER).exists():
        return AppRole.MANAGER
    if user.groups.filter(name=AppRole.EMPLOYEE).exists():
        return AppRole.EMPLOYEE
    if hasattr(user, 'employee_profile'):
        return AppRole.EMPLOYEE
    return 'unassigned'


class Team(models.Model):
    name = models.CharField(max_length=150, unique=True)
    manager = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_teams',
        limit_choices_to={'is_staff': True},
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Employee(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        BUSY = 'busy', 'Busy'
        OVERLOADED = 'overloaded', 'Overloaded'
        ON_LEAVE = 'on_leave', 'On Leave'

    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employee_profile',
    )
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    job_title = models.CharField(max_length=150, blank=True)
    skills = models.TextField(blank=True)
    experience = models.PositiveIntegerField(default=0)
    availability = models.CharField(max_length=20, choices=Availability.choices, default=Availability.AVAILABLE)
    weekly_capacity_hours = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('40.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name

    @property
    def skill_list(self) -> list[str]:
        return [item.strip().lower() for item in self.skills.split(',') if item.strip()]


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNING = 'planning', 'Planning'
        ACTIVE = 'active', 'Active'
        COMPLETED = 'completed', 'Completed'

    name = models.CharField(max_length=180)
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name='projects')
    description = models.TextField(blank=True)
    deadline = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['deadline', 'name']
        unique_together = ('name', 'team')

    def __str__(self) -> str:
        return self.name

    @property
    def progress(self) -> int:
        task_progress = list(self.tasks.values_list('progress', flat=True))
        if not task_progress:
            return 0
        return round(sum(task_progress) / len(task_progress))


class Task(models.Model):
    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        IN_PROGRESS = 'in_progress', 'In Progress'
        SUBMITTED = 'submitted', 'Submitted for Approval'
        COMPLETED = 'completed', 'Completed'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tasks',
    )
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    deadline = models.DateField()
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    progress = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    required_skills = models.TextField(blank=True)
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('8.00'))
    difficulty = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    requires_approval = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['deadline', 'title']

    def __str__(self) -> str:
        return self.title

    @property
    def required_skill_list(self) -> list[str]:
        return [item.strip().lower() for item in self.required_skills.split(',') if item.strip()]

    @property
    def is_delayed(self) -> bool:
        if self.status == self.Status.COMPLETED and self.completed_at:
            return self.completed_at.date() > self.deadline
        return self.status != self.Status.COMPLETED and timezone.localdate() > self.deadline

    @property
    def latest_approval_request(self):
        return self.requests.filter(request_type=Request.Type.TASK_APPROVAL).order_by('-created_at').first()

    def save(self, *args, **kwargs):
        self.progress = max(0, min(int(self.progress or 0), 100))
        if self.progress >= 100:
            self.progress = 100
            self.status = self.Status.SUBMITTED if self.requires_approval else self.Status.COMPLETED
        elif self.progress > 0 and self.status == self.Status.PENDING:
            self.status = self.Status.IN_PROGRESS

        if self.status == self.Status.COMPLETED:
            self.completed_at = self.completed_at or timezone.now()
            self.progress = 100
        elif self.status != self.Status.SUBMITTED:
            self.completed_at = None
        super().save(*args, **kwargs)


class TaskLog(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='logs')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='task_logs')
    hours_spent = models.DecimalField(max_digits=6, decimal_places=2)
    log_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-log_date', '-id']

    def __str__(self) -> str:
        return f'{self.task} - {self.employee} - {self.log_date}'


class AIResult(models.Model):
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name='ai_result')
    predicted_time = models.FloatField(default=0)
    delay_risk = models.FloatField(default=0)
    suggestions = models.TextField(blank=True)
    recommended_action = models.TextField(blank=True)
    workload_signal = models.CharField(max_length=50, blank=True)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-generated_at']

    def __str__(self) -> str:
        return f'AI Result for {self.task}'


class Request(models.Model):
    class Type(models.TextChoices):
        LEAVE = 'leave', 'Leave'
        TASK_APPROVAL = 'task_approval', 'Task Approval'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        REWORK = 'rework', 'Rework Required'

    request_type = models.CharField(max_length=20, choices=Type.choices)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, null=True, blank=True, related_name='requests')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='requests')
    raised_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='raised_requests')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_requests')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.get_request_type_display()} - {self.employee}'


class Activity(models.Model):
    class Type(models.TextChoices):
        NOTIFICATION = 'notification', 'Notification'
        COMMENT = 'comment', 'Comment'
        REVIEW = 'review', 'Review'
        AUDIT = 'audit', 'Audit'

    activity_type = models.CharField(max_length=20, choices=Type.choices)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    task = models.ForeignKey(Task, on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    title = models.CharField(max_length=180, blank=True)
    message = models.TextField()
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.title or self.get_activity_type_display()


class Attachment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='attachments')
    file = models.FileField(upload_to='task_attachments/')
    label = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.label
