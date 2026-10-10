"""All state changes share a PostgreSQL row gate, then validate and commit atomically.

Deliberate course-scale tradeoff: serialise mutations, never read-only queries. The
gate is a DATABASE lock, effective across threads/processes/worker instances.
"""
import hashlib
import json
import uuid
from contextvars import ContextVar
from datetime import timedelta, datetime
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from .models import *

trace_id = ContextVar('trace_id', default='system')

def current_time(): return timezone.now()

class BusinessError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field

def get(model, pk):
    try: return model.objects.get(pk=pk)
    except (model.DoesNotExist, ValueError, TypeError):
        raise BusinessError('记录不存在或编号无效。')

def command(model):
    def decorate(fn):
        @wraps(fn)
        def wrapper(actor, *args, key=None, now=None, **kwargs):
            now = now or current_time()
            key = key or str(uuid.uuid4())
            if not isinstance(key, str) or not 1 <= len(key) <= 100:
                raise BusinessError('请求标识无效，请刷新表单。')
            digest = hashlib.sha256(json.dumps([args, kwargs], sort_keys=True, default=str).encode()).hexdigest()
            with transaction.atomic():
                BusinessConfig.objects.select_for_update().get(pk=1)
                actor = get(User, actor.pk)
                if not actor.is_active: raise PermissionDenied('账号已停用')
                receipt = CommandReceipt.objects.filter(actor=actor, key=key).first()
                if receipt:
                    if receipt.operation != fn.__name__ or receipt.digest != digest:
                        raise BusinessError('同一请求标识不能用于不同操作或不同内容。')
                    return get(model, receipt.result_id)
                token = trace_id.set(key)
                try:
                    result = fn(actor, *args, now=now, **kwargs)
                    CommandReceipt.objects.create(actor=actor, key=key, operation=fn.__name__, digest=digest, result_id=result.pk)
                    return result
                finally:
                    trace_id.reset(token)
        return wrapper
    return decorate

def require_role(actor, *roles):
    if not actor.is_active or actor.role not in roles:
        raise PermissionDenied('当前角色无此操作权限。')

def require_manager(actor, equipment):
    require_role(actor, User.Role.MANAGER)
    if not actor.managed_labs.filter(pk=equipment.laboratory_id).exists():
        raise PermissionDenied('该实验室不在您的授权范围。')

def audit(obj, old, new, actor, reason, now):
    return Audit.objects.create(object_type=obj.__class__.__name__, object_id=obj.pk,
        old_state=str(old), new_state=str(new), actor=actor, reason=reason, at=now, trace=trace_id.get())

def notify(user, event_key, message, now):
    Notification.objects.get_or_create(event_key=event_key, defaults={
        'recipient': user, 'message': message, 'created_at': now, 'next_attempt': now})

def transition(obj, state, actor, reason, now):
    old = obj.status
    obj.status = state
    if isinstance(obj, Reservation): obj.reason = reason
    obj.save()
    audit(obj, old, state, actor, reason, now)
    if isinstance(obj, Reservation):
        notify(obj.applicant, f'reservation:{obj.pk}:{state}',
            f'预约 #{obj.pk} · {obj.equipment.tag}：{obj.get_status_display()}。{reason}', now)

def available(equipment, start, end, exclude=None, now=None):
    now = now or timezone.now()
    if equipment.service != Equipment.Service.NORMAL:
        return False, f'设备服务状态：{equipment.get_service_display()}'
    if equipment.work_orders.exclude(status='closed').exists(): return False, '存在开放维修工单'
    q = Reservation.objects.filter(equipment=equipment, status='approved', start__lt=end, end__gt=start)
    if exclude: q = q.exclude(pk=exclude)
    if q.exists(): return False, '该时段已有通过的预约'
    loans = Loan.objects.filter(equipment=equipment, closed_at__isnull=True)
    for loan in loans:
        if loan.due_at <= now: return False, '当前设备逾期未归还，恢复时间未知'
        if start < loan.due_at and end > loan.issued_at: return False, '当前借出占用该时段'
    return True, '时段空闲（待审核不占用，审核时再次检查）'

def eligibility(user, equipment, end, now):
    if not user.is_active: raise BusinessError('申请人账号已停用。')
    if Loan.objects.filter(borrower=user, closed_at__isnull=True, due_at__lt=now).exists():
        raise BusinessError('申请人存在逾期未归还记录。')
    code = equipment.qualification or ('safety' if equipment.high_value or equipment.dangerous else '')
    if code and not Qualification.objects.filter(user=user, code=code, expires_at__gte=end).exists():
        raise BusinessError(f'缺少覆盖预约结束时间的培训资格：{code}。')

def personal_conflict(user, start, end, now, exclude=None):
    q = Reservation.objects.filter(applicant=user, status__in=['pending','approved'], start__lt=end, end__gt=start)
    if exclude: q = q.exclude(pk=exclude)
    if q.exists(): raise BusinessError('与本人待审核或已通过的预约时间冲突。')
    for loan in Loan.objects.filter(borrower=user, closed_at__isnull=True):
        if loan.due_at <= now or (start < loan.due_at and end > loan.issued_at):
            raise BusinessError('与本人未结束借用时间冲突。')

def validate_time(start, end, now):
    cfg = BusinessConfig.objects.get(pk=1)
    if not isinstance(start, datetime) or not isinstance(end, datetime) or timezone.is_naive(start) or timezone.is_naive(end):
        raise BusinessError('请输入有效的带时区日期时间。', 'start')
    if start >= end: raise BusinessError('结束时间必须晚于开始时间。', 'end')
    if start < now: raise BusinessError('开始时间不得早于当前时间。', 'start')
    if start > now + timedelta(days=cfg.advance_days): raise BusinessError(f'最多提前 {cfg.advance_days} 天预约。', 'start')
    if end - start > timedelta(hours=cfg.max_hours): raise BusinessError(f'单次最长 {cfg.max_hours} 小时。', 'end')
    for value, field in [(start,'start'), (end,'end')]:
        local = timezone.localtime(value)
        if (local.hour*60+local.minute) % cfg.slot_minutes or local.second or local.microsecond:
            raise BusinessError(f'时间须按 {cfg.slot_minutes} 分钟对齐。', field)

def expire_locked(now):
    cfg = BusinessConfig.objects.get(pk=1)
    count = 0
    for r in Reservation.objects.filter(status__in=['pending','approved']).order_by('id'):
        if (r.status == 'pending' and now >= r.start) or (r.status == 'approved' and now >= r.start + timedelta(minutes=cfg.grace_minutes)):
            transition(r, 'expired', None, '未及时审核' if r.status == 'pending' else '超过领取宽限期', now)
            count += 1
    return count

@command(Reservation)
def reserve(actor, equipment_id, start, end, purpose, *, now):
    require_role(actor, User.Role.STUDENT, User.Role.TEACHER)
    expire_locked(now)
    e = get(Equipment, equipment_id)
    validate_time(start, end, now)
    if not purpose.strip() or len(purpose) > 300: raise BusinessError('请填写不超过300字的预约用途。', 'purpose')
    eligibility(actor, e, end, now)
    personal_conflict(actor, start, end, now)
    cfg = BusinessConfig.objects.get(pk=1)
    if Reservation.objects.filter(applicant=actor, status__in=['pending','approved'], start__gte=now).count() >= cfg.max_future:
        raise BusinessError(f'未来待审核和已通过预约最多 {cfg.max_future} 条。')
    free, reason = available(e, start, end, now=now)
    if not free: raise BusinessError(reason)
    r = Reservation.objects.create(applicant=actor, equipment=e, start=start, end=end, purpose=purpose,
        created_at=now, demo=actor.demo and e.demo)
    audit(r, '', 'pending', actor, purpose, now)
    notify(actor, f'reservation:{r.pk}:pending', f'预约 #{r.pk} 已提交，等待人工审核。', now)
    return r

@command(Reservation)
def review(actor, reservation_id, approve, reason, *, now):
    r = get(Reservation, reservation_id)
    require_manager(actor, r.equipment)
    if r.status != 'pending': raise BusinessError('仅待审核预约可以审核。')
    if now >= r.start: raise BusinessError('预约已到开始时间，不能再批准。')
    if not reason.strip(): raise BusinessError('请填写审核原因。', 'reason')
    if approve:
        eligibility(r.applicant, r.equipment, r.end, now)
        personal_conflict(r.applicant, r.start, r.end, now, exclude=r.pk)
        free, why = available(r.equipment, r.start, r.end, exclude=r.pk, now=now)
        if not free: raise BusinessError(why)
    Review.objects.create(reservation=r, reviewer=actor, approved=approve, reason=reason, created_at=now)
    transition(r, 'approved' if approve else 'rejected', actor, reason, now)
    return r

@command(Reservation)
def cancel(actor, reservation_id, reason, *, now):
    r = get(Reservation, reservation_id)
    if r.applicant_id != actor.pk: raise PermissionDenied('只能取消本人预约。')
    if r.status not in ['pending','approved'] or now >= r.start:
        raise BusinessError('只能取消尚未开始的待审核或已通过预约。')
    if not reason.strip(): raise BusinessError('请填写取消原因。', 'reason')
    transition(r, 'cancelled', actor, reason, now)
    return r

@command(Loan)
def issue(actor, reservation_id, borrower_username, equipment_tag, accessories_checked, inspection, *, now):
    r = get(Reservation, reservation_id)
    e = r.equipment
    require_manager(actor, e)
    if r.status != 'approved': raise BusinessError('借出需要已通过预约；已借出记录不能重复办理。')
    cfg = BusinessConfig.objects.get(pk=1)
    if not r.start <= now < min(r.end, r.start + timedelta(minutes=cfg.grace_minutes)):
        raise BusinessError('不在允许领取时间内（开始时间至领取宽限期或结束时间，以较早者为准）。')
    if r.applicant.username != borrower_username: raise BusinessError('实际借用人身份与预约申请人不匹配。', 'borrower_username')
    if equipment_tag != e.tag: raise BusinessError('实物设备标签与预约不匹配。', 'equipment_tag')
    if not accessories_checked: raise BusinessError('请核对配件清单。', 'accessories_checked')
    if not inspection.strip(): raise BusinessError('请记录正常外观及功能检查结果；设备异常时先登记异常。', 'inspection')
    eligibility(r.applicant, e, r.end, now)
    free, why = available(e, now, r.end, exclude=r.pk, now=now)
    if not free: raise BusinessError(why)
    if e.physical != 'stock' or Loan.objects.filter(equipment=e, closed_at__isnull=True).exists():
        raise BusinessError('设备有未结束借出，不能重复借出。')
    loan = Loan.objects.create(reservation=r, equipment=e, borrower=r.applicant, operator=actor,
        issued_at=now, due_at=r.end, inspection=inspection,
        accessories=list(e.accessories.values('name','quantity')))
    e.physical = 'out'; e.save(update_fields=['physical'])
    audit(loan, '', 'out', actor, inspection, now)
    transition(r, 'borrowed', actor, '身份、标签、配件和外观功能检查通过', now)
    return loan

def invalidate_future(e, actor, reason, now):
    for r in Reservation.objects.filter(equipment=e, status__in=['pending','approved'], start__gt=now):
        transition(r, 'cancelled', actor, f'设备不可履约：{reason}', now)
    for loan in Loan.objects.filter(equipment=e, closed_at__isnull=True):
        for manager in e.laboratory.managers.filter(is_active=True):
            notify(manager, f'incident-in-use:{e.pk}:{trace_id.get()}:{manager.pk}',
                f'{e.tag} 在借出中发生异常/维护。借出事实保留，请联系借用人处理。', now)

def set_service(e, new, actor, reason, now):
    old = e.service
    if old == new: return
    if old == 'normal' and new != 'normal':
        ServiceInterval.objects.create(equipment=e, start=now)
    elif old != 'normal' and new == 'normal':
        ServiceInterval.objects.filter(equipment=e, end__isnull=True).update(end=now)
    e.service = new; e.save(update_fields=['service'])
    audit(e, old, new, actor, reason, now)
    if new != 'normal': invalidate_future(e, actor, reason, now)

def try_restore(e, actor, reason, now):
    if e.service in ['disabled','retired']: return False
    if e.incidents.exclude(status='resolved').exists(): return False
    if e.work_orders.exclude(status='closed').exists(): return False
    if Loan.objects.filter(equipment=e, closed_at__isnull=True).exists(): return False
    set_service(e, 'normal', actor, reason, now)
    return True

@command(ReturnRecord)
def receive(actor, loan_id, condition, notes, *, now):
    loan = get(Loan, loan_id)
    e = loan.equipment
    require_manager(actor, e)
    if loan.closed_at is not None: raise BusinessError('该借出已经归还，不能重复归还。')
    if condition not in ReturnRecord.Condition.values: raise BusinessError('归还检查结果无效。', 'condition')
    if not notes.strip(): raise BusinessError('请填写检查结果及配件情况。', 'notes')
    if now < loan.issued_at: raise BusinessError('接收时间不能早于借出时间。')
    result = ReturnRecord.objects.create(loan=loan, receiver=actor, received_at=now, condition=condition,
        notes=notes, overdue_seconds=max(0, int((now-loan.due_at).total_seconds())))
    loan.closed_at = now; loan.save(update_fields=['closed_at'])
    e.physical = 'stock'; e.save(update_fields=['physical'])
    if condition != 'normal':
        incident = Incident.objects.create(equipment=e, reporter=actor, loan=loan,
            description=f'{result.get_condition_display()}：{notes}', status='confirmed', created_at=now)
        audit(incident, '', 'confirmed', actor, notes, now)
        if e.service not in ['disabled','retired']: set_service(e, 'quarantine', actor, notes, now)
    else: try_restore(e, actor, '正常归还且无其他阻塞', now)
    audit(result, '', condition, actor, notes, now)
    transition(loan.reservation, 'completed', actor, f'归还检查：{result.get_condition_display()}', now)
    return result

@command(Incident)
def report_incident(actor, equipment_id, description, *, now):
    e = get(Equipment, equipment_id)
    if actor.role == 'manager': require_manager(actor, e)
    else:
        require_role(actor, 'student', 'teacher')
        if not Loan.objects.filter(equipment=e, borrower=actor).exists():
            raise PermissionDenied('仅可为本人借用过的设备提交异常说明。')
    if not description.strip(): raise BusinessError('请说明异常情况。', 'description')
    loan = Loan.objects.filter(equipment=e, borrower=actor).order_by('-id').first()
    incident = Incident.objects.create(equipment=e, reporter=actor, loan=loan, description=description, created_at=now)
    audit(incident, '', 'pending', actor, description, now)
    if e.service not in ['disabled','retired']: set_service(e, 'quarantine', actor, description, now)
    else: invalidate_future(e, actor, description, now)
    for m in e.laboratory.managers.filter(is_active=True):
        notify(m, f'incident:{incident.pk}:manager:{m.pk}', f'{e.tag} 异常待确认：{description}', now)
    return incident

@command(Incident)
def confirm_incident(actor, incident_id, decision, reason, *, now):
    i = get(Incident, incident_id); e = i.equipment
    require_manager(actor, e)
    if i.status == 'resolved': raise BusinessError('异常已经解决。')
    if decision not in ['confirm','resolve'] or not reason.strip(): raise BusinessError('请选择核验结论并填写原因。')
    if decision == 'resolve' and i.work_orders.exclude(status='closed').exists():
        raise BusinessError('该异常仍有开放工单，不能直接解决。')
    old = i.status
    i.status = 'confirmed' if decision == 'confirm' else 'resolved'
    i.resolution = reason
    if i.status == 'resolved': i.resolved_at = now
    i.save(); audit(i, old, i.status, actor, reason, now)
    if decision == 'resolve': try_restore(e, actor, reason, now)
    return i

@command(WorkOrder)
def create_work_order(actor, incident_id, assignee_id, reason, *, now):
    i = get(Incident, incident_id); e = i.equipment
    require_manager(actor, e)
    if i.status != 'confirmed': raise BusinessError('须先确认异常，才能创建维修工单。')
    tech = get(User, assignee_id)
    if tech.role != 'technician' or not tech.is_active: raise BusinessError('请选择有效维修人员。', 'assignee_id')
    if not reason.strip(): raise BusinessError('请填写维修要求。', 'reason')
    w = WorkOrder.objects.create(equipment=e, incident=i, assignee=tech, creator=actor, progress=reason, created_at=now)
    audit(w, '', 'open', actor, reason, now)
    if e.service not in ['disabled','retired']: set_service(e, 'repair', actor, reason, now)
    notify(tech, f'work:{w.pk}:assigned', f'维修工单 #{w.pk}：{e.tag}。{reason}', now)
    return w

@command(WorkOrder)
def progress_work(actor, work_id, submit, text, *, now):
    w = get(WorkOrder, work_id)
    require_role(actor, 'technician')
    if w.assignee_id != actor.pk: raise PermissionDenied('只能处理本人被指派的工单。')
    if w.status not in ['open','progress']: raise BusinessError('工单当前状态不能更新维修。')
    if not text.strip(): raise BusinessError('请填写维修进度或结果。', 'text')
    if submit: w.result = text
    else: w.progress = text
    transition(w, 'acceptance' if submit else 'progress', actor, text, now)
    return w

@command(WorkOrder)
def accept_work(actor, work_id, approved, reason, *, now):
    w = get(WorkOrder, work_id); e = w.equipment
    require_manager(actor, e)
    if w.status != 'acceptance': raise BusinessError('仅待验收工单可以验收。')
    if not reason.strip(): raise BusinessError('请填写验收结论。', 'reason')
    if approved:
        w.accepted_by = actor; w.closed_at = now
        transition(w, 'closed', actor, reason, now)
        i = w.incident
        if not i.work_orders.exclude(status='closed').exists():
            old = i.status; i.status='resolved'; i.resolution=reason; i.resolved_at=now; i.save()
            audit(i, old, 'resolved', actor, reason, now)
        try_restore(e, actor, reason, now)
    else: transition(w, 'progress', actor, reason, now)
    return w

@command(Equipment)
def maintain_equipment(actor, equipment_id, name, category, description, qualification, high_value, dangerous, service, reason, *, now):
    e = get(Equipment, equipment_id)
    require_manager(actor, e)
    if not name.strip() or not category.strip() or not reason.strip(): raise BusinessError('名称、类型和维护原因必填。')
    if service not in Equipment.Service.values: raise BusinessError('设备服务状态无效。')
    if (high_value or dangerous) and not qualification.strip(): raise BusinessError('高价值或危险设备必须指定培训资格。', 'qualification')
    e.name=name; e.category=category; e.description=description; e.qualification=qualification
    e.high_value=high_value; e.dangerous=dangerous; e.save()
    if service == 'normal':
        if e.service in ['disabled','retired']:
            # Explicit re-enable; still never bypass unresolved incidents/orders/loans.
            if e.incidents.exclude(status='resolved').exists() or e.work_orders.exclude(status='closed').exists() or Loan.objects.filter(equipment=e, closed_at__isnull=True).exists():
                raise BusinessError('仍有异常、工单或未归还借出，不能恢复。')
            set_service(e, 'normal', actor, reason, now)
        elif not try_restore(e, actor, reason, now) and e.service != 'normal':
            raise BusinessError('仍有异常、工单或未归还借出，不能恢复。')
    else: set_service(e, service, actor, reason, now)
    # A new safety qualification may invalidate existing applicants too.
    for r in e.reservations.filter(status__in=['pending','approved'], start__gt=now):
        try: eligibility(r.applicant, e, r.end, now)
        except BusinessError: transition(r, 'cancelled', actor, '设备培训要求变更，原预约资格不足', now)
    audit(e, 'metadata', 'updated', actor, reason, now)
    return e

@command(User)
def save_user(actor, user_id, username, role, active, labs, password, *, now):
    require_role(actor, 'system')
    if role not in User.Role.values: raise BusinessError('角色无效。')
    if actor.pk == user_id and (role != 'system' or not active): raise BusinessError('不能撤销当前管理员自己的管理权限。')
    u = get(User, user_id) if user_id else User(username=username)
    if not username.strip() or User.objects.filter(username=username).exclude(pk=u.pk).exists(): raise BusinessError('用户名为空或已存在。', 'username')
    if not u.pk and not password: raise BusinessError('新用户必须设置密码。', 'password')
    old = f'{u.role}/{u.is_active}' if u.pk else ''
    u.username=username; u.role=role; u.is_active=active
    if password: validate_password(password, u); u.set_password(password)
    u.save(); u.managed_labs.set(labs if role == 'manager' else [])
    audit(u, old, f'{role}/{active}', actor, '用户与实验室授权更新', now)
    return u

@command(Laboratory)
def save_lab(actor, lab_id, code, name, location, opening, *, now):
    require_role(actor, 'system')
    lab = get(Laboratory, lab_id) if lab_id else Laboratory()
    if not all([code.strip(),name.strip(),location.strip(),opening.strip()]): raise BusinessError('实验室各字段必填。')
    if Laboratory.objects.filter(code=code).exclude(pk=lab.pk).exists(): raise BusinessError('实验室编号已存在。', 'code')
    lab.code=code; lab.name=name; lab.location=location; lab.opening=opening; lab.save()
    audit(lab, '', 'updated', actor, '基础数据维护', now)
    return lab

@command(Qualification)
def save_qualification(actor, user_id, code, expires_at, *, now):
    require_role(actor, 'system')
    if not code.strip() or expires_at <= now: raise BusinessError('资格代码必填，有效期须晚于现在。')
    u = get(User, user_id)
    q, _ = Qualification.objects.update_or_create(user=u, code=code, defaults={'expires_at':expires_at})
    audit(q, '', 'updated', actor, '模拟培训资格维护', now)
    return q

@command(BusinessConfig)
def save_config(actor, advance_days, slot_minutes, max_hours, max_future, grace_minutes, *, now):
    require_role(actor, 'system')
    values = dict(advance_days=advance_days, slot_minutes=slot_minutes, max_hours=max_hours, max_future=max_future, grace_minutes=grace_minutes)
    if any(not isinstance(v,int) or v<=0 for v in values.values()) or slot_minutes not in [15,30,60]:
        raise BusinessError('参数须为正整数，时间粒度可选15、30或60分钟。')
    cfg = BusinessConfig.objects.get(pk=1)
    old=json.dumps({k:getattr(cfg,k) for k in values})
    for k,v in values.items(): setattr(cfg,k,v)
    cfg.save(); audit(cfg, 'parameters', 'updated', actor, old+' → '+json.dumps(values), now)
    return cfg

@command(Equipment)
def add_equipment(actor, laboratory_id, tag, name, category, qualification, high_value, dangerous, accessories, *, now):
    lab=get(Laboratory,laboratory_id)
    e=Equipment(laboratory=lab)
    require_manager(actor,e)
    if not all([tag.strip(),name.strip(),category.strip()]): raise BusinessError('标签、名称、类型必填。')
    if Equipment.objects.filter(tag=tag).exists(): raise BusinessError('设备标签已存在。','tag')
    if (high_value or dangerous) and not qualification.strip(): raise BusinessError('须指定培训资格。','qualification')
    e.tag=tag; e.name=name; e.category=category; e.qualification=qualification; e.high_value=high_value; e.dangerous=dangerous; e.save()
    for accessory in set(x.strip() for x in accessories.split(',') if x.strip()):
        Accessory.objects.create(equipment=e,name=accessory,quantity=1)
    audit(e,'','normal',actor,'新建设备与配件清单',now)
    return e
