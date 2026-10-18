def navigation(request):
    if not request.user.is_authenticated:return {}
    codes=set(request.user.roles.values_list("code",flat=True))
    return {"role_codes":codes,"can_manage":"LAB_ADMIN" in codes,"can_repair":"TECHNICIAN" in codes,"can_system":"SYS_ADMIN" in codes,"can_report":bool(codes & {"DIRECTOR","LAB_ADMIN"}),"can_audit":bool(codes & {"DIRECTOR","LAB_ADMIN","SYS_ADMIN"}),"unread_count":request.user.notifications.filter(read_at__isnull=True).count()}
