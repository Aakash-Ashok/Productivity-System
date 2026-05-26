from .models import AppRole, current_user_role


def role_context(request):
    role = current_user_role(request.user)

    return {
        'current_role': role,
        'is_admin_role': role == AppRole.ADMIN,
        'is_manager_role': role == AppRole.MANAGER,
        'is_employee_role': role == AppRole.EMPLOYEE,
        'can_manage_org': role == AppRole.ADMIN,
        'can_manage_delivery': role in {AppRole.ADMIN, AppRole.MANAGER},
        'can_log_work': role in {AppRole.ADMIN, AppRole.MANAGER, AppRole.EMPLOYEE},
    }
