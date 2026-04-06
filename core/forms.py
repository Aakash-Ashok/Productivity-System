from django import forms
from django.contrib.auth.models import User
from django.utils.crypto import get_random_string

from .models import (
    Employee,
    LeaveRequest,
    Milestone,
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


class DateInput(forms.DateInput):
    input_type = 'date'


class UserAccountForm(forms.ModelForm):
    role = forms.ChoiceField(choices=UserProfile.Role.choices)
    password = forms.CharField(widget=forms.PasswordInput(), required=False, help_text='Leave blank to auto-generate.')

    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def save(self, commit=True):
        user = super().save(commit=False)
        raw_password = self.cleaned_data['password'] or get_random_string(10)
        user.set_password(raw_password)
        role = self.cleaned_data['role']
        if role == UserProfile.Role.ADMIN:
            user.is_staff = True
            user.is_superuser = True
        elif role == UserProfile.Role.MANAGER:
            user.is_staff = True
            user.is_superuser = False
        else:
            user.is_staff = False
            user.is_superuser = False
        if commit:
            user.save()
            UserProfile.objects.update_or_create(user=user, defaults={'role': role})
        user.generated_password = raw_password
        user.generated_role = role
        return user


class ProfileForm(forms.ModelForm):
    employee_name = forms.CharField(required=False, label='Display name')
    job_title = forms.CharField(required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        employee = getattr(self.instance, 'employee_profile', None)
        if employee:
            self.fields['employee_name'].initial = employee.name
            self.fields['job_title'].initial = employee.job_title

    def save(self, commit=True):
        user = super().save(commit=commit)
        employee = getattr(user, 'employee_profile', None)
        if employee:
            employee.name = self.cleaned_data['employee_name'] or employee.name
            employee.job_title = self.cleaned_data['job_title']
            employee.email = self.cleaned_data['email']
            employee.save()
        return user


class UserStatusForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['is_active']


class UserAdminEditForm(forms.ModelForm):
    role = forms.ChoiceField(choices=UserProfile.Role.choices)

    class Meta:
        model = User
        fields = ['username', 'email', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['role'].initial = getattr(getattr(self.instance, 'profile', None), 'role', UserProfile.Role.EMPLOYEE)

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data['role']
        user.is_superuser = role == UserProfile.Role.ADMIN
        user.is_staff = role in {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER}
        if commit:
            user.save()
            UserProfile.objects.update_or_create(user=user, defaults={'role': role})
        return user


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'manager', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class EmployeeForm(forms.ModelForm):
    username = forms.CharField(required=False, help_text='Required only when creating a new employee login.')
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(),
        help_text='Leave blank to auto-generate for a new employee login.',
    )

    class Meta:
        model = Employee
        fields = [
            'user',
            'team',
            'name',
            'email',
            'job_title',
            'skills',
            'experience_years',
            'weekly_capacity_hours',
            'availability',
        ]
        widgets = {
            'skills': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        user = cleaned_data.get('user')
        username = cleaned_data.get('username')

        if not user and not username:
            raise forms.ValidationError('Select an existing user or provide a username to create a new employee login.')
        if username and User.objects.filter(username=username).exists():
            self.add_error('username', 'This username is already taken.')
        return cleaned_data

    def save(self, commit=True):
        employee = super().save(commit=False)
        created_user = None
        created_password = None

        if employee.user is None:
            username = self.cleaned_data['username']
            created_password = self.cleaned_data['password'] or get_random_string(10)
            created_user = User.objects.create_user(
                username=username,
                email=self.cleaned_data['email'],
                password=created_password,
            )
            UserProfile.objects.update_or_create(user=created_user, defaults={'role': UserProfile.Role.EMPLOYEE})
            employee.user = created_user

        if commit:
            employee.save()

        employee.created_user = created_user
        employee.created_password = created_password
        return employee


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'team', 'description', 'deadline', 'status']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'deadline': DateInput(),
        }


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ['project', 'name', 'description', 'deadline', 'status']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'deadline': DateInput(),
        }


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            'project',
            'milestone',
            'title',
            'description',
            'assigned_to',
            'required_skills',
            'deadline',
            'priority',
            'status',
            'difficulty',
            'requires_approval',
            'estimated_hours',
            'progress',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'required_skills': forms.Textarea(attrs={'rows': 3}),
            'deadline': DateInput(),
        }


class TaskUpdateForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['project', 'milestone', 'assigned_to', 'deadline', 'priority', 'status', 'progress', 'requires_approval']
        widgets = {
            'deadline': DateInput(),
        }


class TaskApprovalForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['approval_status', 'manager_remark']
        widgets = {
            'manager_remark': forms.Textarea(attrs={'rows': 4}),
        }


class TaskLogForm(forms.ModelForm):
    class Meta:
        model = TaskLog
        fields = ['task', 'employee', 'hours_spent', 'log_date', 'progress_after_log', 'notes']
        widgets = {
            'log_date': DateInput(),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        employee = kwargs.pop('employee', None)
        role = kwargs.pop('role', None)
        super().__init__(*args, **kwargs)

        if employee is not None:
            self.fields['task'].queryset = Task.objects.filter(assigned_to=employee).exclude(status=Task.Status.COMPLETED)
            self.fields['employee'].queryset = Employee.objects.filter(pk=employee.pk)
            self.fields['employee'].initial = employee
            if role == UserProfile.Role.EMPLOYEE:
                self.fields['employee'].widget = forms.HiddenInput()

        self.fields['progress_after_log'].help_text = 'Update the current progress for this task.'


class PerformanceReviewForm(forms.ModelForm):
    class Meta:
        model = PerformanceReview
        fields = ['employee', 'review_date', 'rating', 'remarks']
        widgets = {
            'review_date': DateInput(),
            'remarks': forms.Textarea(attrs={'rows': 4}),
        }


class TaskCommentForm(forms.ModelForm):
    class Meta:
        model = TaskComment
        fields = ['body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Add a comment...'}),
        }


class TaskAttachmentForm(forms.ModelForm):
    class Meta:
        model = TaskAttachment
        fields = ['label', 'file']


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['start_date', 'end_date', 'reason']
        widgets = {
            'start_date': DateInput(),
            'end_date': DateInput(),
            'reason': forms.Textarea(attrs={'rows': 3}),
        }


class LeaveApprovalForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['status', 'manager_note']
        widgets = {
            'manager_note': forms.Textarea(attrs={'rows': 3}),
        }


class RecurringTaskForm(forms.ModelForm):
    class Meta:
        model = RecurringTask
        fields = ['project', 'title', 'description', 'assigned_to', 'frequency', 'next_due_date', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'next_due_date': DateInput(),
        }
