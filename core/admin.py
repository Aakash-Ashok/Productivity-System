from django.contrib import admin

from .models import AIResult, Activity, Attachment, Employee, Project, Request, Task, TaskLog, Team


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'manager', 'created_at')
    search_fields = ('name',)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'team', 'job_title', 'experience', 'availability')
    list_filter = ('team', 'availability')
    search_fields = ('name', 'email', 'skills')


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'team', 'deadline', 'status')
    list_filter = ('team', 'status')
    search_fields = ('name', 'description')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'assigned_to', 'priority', 'status', 'deadline', 'progress')
    list_filter = ('priority', 'status', 'project')
    search_fields = ('title', 'description', 'required_skills')


@admin.register(TaskLog)
class TaskLogAdmin(admin.ModelAdmin):
    list_display = ('task', 'employee', 'hours_spent', 'log_date')
    list_filter = ('log_date',)


@admin.register(AIResult)
class AIResultAdmin(admin.ModelAdmin):
    list_display = ('task', 'predicted_time', 'delay_risk', 'generated_at')


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('request_type', 'employee', 'task', 'status', 'created_at')
    list_filter = ('request_type', 'status')


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ('activity_type', 'user', 'task', 'project', 'title', 'created_at', 'is_read')
    list_filter = ('activity_type', 'is_read')
    search_fields = ('title', 'message')


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('task', 'label', 'uploaded_by', 'created_at')
