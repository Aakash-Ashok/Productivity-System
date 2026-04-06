from .models import UserProfile


def role_context(request):
    role = 'guest'
    if request.user.is_authenticated:
        if request.user.is_superuser:
            role = UserProfile.Role.ADMIN
        else:
            role = getattr(getattr(request.user, 'profile', None), 'role', 'unassigned')

    return {
        'current_role': role,
        'is_admin_role': role == UserProfile.Role.ADMIN,
        'is_manager_role': role == UserProfile.Role.MANAGER,
        'is_employee_role': role == UserProfile.Role.EMPLOYEE,
        'can_manage_org': role == UserProfile.Role.ADMIN,
        'can_manage_delivery': role in {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER},
        'can_log_work': role in {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER, UserProfile.Role.EMPLOYEE},
    }
