from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.crypto import get_random_string

from .models import Activity, AppRole, Attachment, Employee, Project, Request, Task, TaskLog, Team, assign_user_role


class DateInput(forms.DateInput):
    input_type = 'date'


class UserAccountForm(forms.ModelForm):
    role = forms.ChoiceField(choices=AppRole.choices)
    password = forms.CharField(widget=forms.PasswordInput(), required=False, help_text='Leave blank to auto-generate.')

    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def save(self, commit=True):
        user = super().save(commit=False)
        raw_password = self.cleaned_data['password'] or get_random_string(10)
        user.set_password(raw_password)
        if commit:
            user.save()
            assign_user_role(user, self.cleaned_data['role'])
        user.generated_password = raw_password
        user.generated_role = self.cleaned_data['role']
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


class UserAdminEditForm(forms.ModelForm):
    role = forms.ChoiceField(choices=AppRole.choices)

    class Meta:
        model = User
        fields = ['username', 'email', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            if self.instance.is_superuser:
                self.fields['role'].initial = AppRole.ADMIN
            elif self.instance.groups.filter(name=AppRole.MANAGER).exists():
                self.fields['role'].initial = AppRole.MANAGER
            else:
                self.fields['role'].initial = AppRole.EMPLOYEE

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            assign_user_role(user, self.cleaned_data['role'])
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
            'experience',
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
            assign_user_role(created_user, AppRole.EMPLOYEE)
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


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            'project',
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
        fields = ['project', 'assigned_to', 'deadline', 'priority', 'status', 'progress', 'requires_approval']
        widgets = {
            'deadline': DateInput(),
        }


class TaskApprovalForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = ['status', 'remarks']
        widgets = {
            'remarks': forms.Textarea(attrs={'rows': 4}),
        }


class TaskLogForm(forms.ModelForm):
    progress_after_log = forms.IntegerField(min_value=0, max_value=100, required=False, label='Current progress')

    class Meta:
        model = TaskLog
        fields = ['task', 'employee', 'hours_spent', 'log_date', 'notes']
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
            if role == AppRole.EMPLOYEE:
                self.fields['employee'].widget = forms.HiddenInput()

        self.fields['progress_after_log'].help_text = 'Update the current progress for this task.'


class PerformanceReviewForm(forms.ModelForm):
    employee = forms.ModelChoiceField(queryset=Employee.objects.none())
    review_date = forms.DateField(initial=timezone.localdate, widget=DateInput())

    class Meta:
        model = Activity
        fields = ['title', 'message', 'rating']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        employee_queryset = kwargs.pop('employee_queryset', Employee.objects.none())
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = employee_queryset


class TaskCommentForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ['message']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Add a comment...'}),
        }


class TaskAttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ['label', 'file']


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = ['start_date', 'end_date', 'remarks']
        widgets = {
            'start_date': DateInput(),
            'end_date': DateInput(),
            'remarks': forms.Textarea(attrs={'rows': 3}),
        }


class LeaveApprovalForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = ['status', 'remarks']
        widgets = {
            'remarks': forms.Textarea(attrs={'rows': 3}),
        }
