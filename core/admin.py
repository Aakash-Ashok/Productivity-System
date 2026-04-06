from django.contrib import admin

from .models import (
    AIResult,
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


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'manager', 'created_at')
    search_fields = ('name',)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'team', 'job_title', 'experience_years', 'availability')
    list_filter = ('team', 'availability')
    search_fields = ('name', 'email', 'skills')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'team', 'deadline', 'status')
    list_filter = ('team', 'status')
    search_fields = ('name', 'description')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'assigned_to', 'priority', 'status', 'approval_status', 'deadline', 'progress')
    list_filter = ('priority', 'status', 'approval_status', 'project')
    search_fields = ('title', 'description', 'required_skills')


@admin.register(TaskLog)
class TaskLogAdmin(admin.ModelAdmin):
    list_display = ('task', 'employee', 'hours_spent', 'log_date', 'progress_after_log')
    list_filter = ('log_date',)


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'deadline', 'status')


@admin.register(RecurringTask)
class RecurringTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'assigned_to', 'frequency', 'next_due_date', 'is_active')


@admin.register(AIResult)
class AIResultAdmin(admin.ModelAdmin):
    list_display = ('task', 'predicted_time', 'delay_risk', 'generated_at')


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = ('employee', 'manager', 'review_date', 'rating')
    list_filter = ('review_date', 'rating')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'title', 'kind', 'is_read', 'created_at')
    list_filter = ('kind', 'is_read')


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ('task', 'author', 'created_at')


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ('task', 'label', 'uploaded_by', 'uploaded_at')


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('employee', 'start_date', 'end_date', 'status')


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'entity_type', 'entity_id')


@admin.register(PerformanceMetric)
class PerformanceMetricAdmin(admin.ModelAdmin):
    list_display = (
        'employee',
        'tasks_completed',
        'avg_completion_time',
        'delayed_tasks',
        'active_tasks',
        'burnout_risk',
    )
