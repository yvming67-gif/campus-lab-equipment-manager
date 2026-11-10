"""Versioned, source-bound safety information for room resources."""
from django.db import transaction
from django.utils import timezone
from . import services as s
from .models import Reservation, Resource, SafetyAcknowledgement, SafetyProcedure

CONTENT_FIELDS = (
    "risk_categories", "applicable_users", "prerequisites", "required_ppe",
    "pre_use_checks", "operating_cautions", "prohibited_actions", "incident_route",
)


def current_procedure(resource):
    if resource.kind != "ROOM":
        return None
    return resource.safety_procedures.filter(
        status="PUBLISHED", effective_from__lte=timezone.localdate(),
        review_due_date__gte=timezone.localdate(),
    ).order_by("-version").first()


def latest_published_procedure(resource):
    """Most recent published version, including an overdue version for context."""
    if resource.kind != "ROOM":
        return None
    return resource.safety_procedures.filter(status="PUBLISHED").order_by("-version").first()


def acknowledged(user, procedure):
    return bool(procedure and SafetyAcknowledgement.objects.filter(user=user, procedure=procedure).exists())


def needs_ack(user, resource):
    procedure = current_procedure(resource)
    return bool(procedure and procedure.requires_acknowledgement and not acknowledged(user, procedure))


def _validate_for_review(data):
    missing = [name for name in CONTENT_FIELDS if not str(data.get(name, "")).strip()]
    for name in ("source_title", "source_reference", "source_version", "source_effective_date", "effective_from", "review_due_date"):
        if not data.get(name):
            missing.append(name)
    if missing:
        raise s.BusinessError("提交审核前，须填写安全信息正文、正式来源、版本、生效日期及复核期限")
    if data["review_due_date"] < timezone.localdate():
        raise s.BusinessError("复核期限不得早于当前日期")
    if data["effective_from"] > timezone.localdate():
        raise s.BusinessError("安全信息只能发布为当前或已生效版本；未来版本须到生效日再发布")
    if data["source_effective_date"] > data["effective_from"]:
        raise s.BusinessError("来源文件生效日期不得晚于本版本生效日期")
    if data["review_due_date"] < data["effective_from"]:
        raise s.BusinessError("复核期限不得早于本版本生效日期")


@transaction.atomic
def save_draft(actor, resource_id, data, procedure_id=None, at=None):
    at = at or timezone.now()
    resource = Resource.objects.select_for_update().select_related("lab").get(pk=resource_id)
    s.check_manager(actor, resource)
    s.require(resource.kind == "ROOM", "安全规程只可关联场地资源")
    values = {name: data.get(name) for name in (
        "title", *CONTENT_FIELDS, "source_title", "source_reference", "source_version",
        "source_effective_date", "effective_from", "review_due_date", "requires_acknowledgement", "change_reason")}
    values["change_reason"] = str(values.get("change_reason") or "").strip()
    s.require(bool(values["title"].strip()), "规程标题不能为空")
    s.require(bool(values["change_reason"]), "编制或修订原因不能为空")
    if procedure_id:
        procedure = SafetyProcedure.objects.select_for_update().get(pk=procedure_id, resource=resource)
        s.require(procedure.status == "DRAFT", "已提交审核的版本不能继续编辑")
        s.require(procedure.created_by_id == actor.pk, "只能编辑本人创建的草稿")
        old = {name: getattr(procedure, name) for name in values}
        for name, value in values.items(): setattr(procedure, name, value)
        procedure.save()
        event = s.audit(actor, procedure, "SAFETY_DRAFT_UPDATE", old=old, new=values, reason=values["change_reason"], at=at)
    else:
        version = (SafetyProcedure.objects.filter(resource=resource).order_by("-version").values_list("version", flat=True).first() or 0) + 1
        procedure = SafetyProcedure.objects.create(resource=resource, version=version, created_by=actor, **values)
        event = s.audit(actor, procedure, "SAFETY_DRAFT_CREATE", new={"version": version, "status": procedure.status}, reason=values["change_reason"], at=at)
    return procedure, event


@transaction.atomic
def submit_for_review(actor, procedure_id, at=None):
    at = at or timezone.now()
    procedure = SafetyProcedure.objects.select_for_update().select_related("resource").get(pk=procedure_id)
    s.check_manager(actor, procedure.resource)
    s.require(procedure.resource.kind == "ROOM", "安全规程只可关联场地资源")
    s.require(procedure.status == "DRAFT" and procedure.created_by_id == actor.pk, "只有草稿创建人可以提交审核")
    _validate_for_review({name: getattr(procedure, name) for name in (*CONTENT_FIELDS, "source_title", "source_reference", "source_version", "source_effective_date", "effective_from", "review_due_date")})
    old = procedure.status
    procedure.status = "PENDING_REVIEW"
    procedure.submitted_at = at
    procedure.save(update_fields=["status", "submitted_at"])
    s.audit(actor, procedure, "SAFETY_SUBMIT_REVIEW", old={"status": old}, new={"status": procedure.status, "version": procedure.version}, reason=procedure.change_reason, at=at)
    return procedure


@transaction.atomic
def publish(actor, procedure_id, review_note, at=None):
    at = at or timezone.now()
    reference = SafetyProcedure.objects.select_related("resource").get(pk=procedure_id)
    # Serialize publication by room before checking the published version. A
    # room may have an overdue PUBLISHED version, which is not "current" for
    # display but still occupies the database's one-published-version slot.
    resource = Resource.objects.select_for_update().get(pk=reference.resource_id)
    procedure = SafetyProcedure.objects.select_for_update().select_related("created_by").get(pk=procedure_id)
    s.check_manager(actor, resource)
    s.require(resource.kind == "ROOM", "安全规程只可关联场地资源")
    s.require(actor.pk != procedure.created_by_id, "规程提交人与审核发布人必须分离")
    s.require(procedure.status == "PENDING_REVIEW", "只有待审核版本可以发布")
    review_note = str(review_note or "").strip()
    s.require(bool(review_note), "发布前必须记录复核意见和来源核验结论")
    s.require(len(review_note) <= 2000, "复核意见不得超过2000个字符")
    _validate_for_review({name: getattr(procedure, name) for name in (*CONTENT_FIELDS, "source_title", "source_reference", "source_version", "source_effective_date", "effective_from", "review_due_date")})
    current = current_procedure(resource)
    published_versions = list(
        SafetyProcedure.objects.select_for_update().filter(resource=resource, status="PUBLISHED").order_by("-version")
    )
    for previous in published_versions:
        previous.status = "SUPERSEDED"
        previous.save(update_fields=["status"])
    procedure.status = "PUBLISHED"
    procedure.reviewed_by = actor
    procedure.reviewed_at = at
    procedure.review_note = review_note
    procedure.published_by = actor
    procedure.published_at = at
    procedure.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "published_by", "published_at"])
    event = s.audit(actor, procedure, "SAFETY_PUBLISH", old={
        "status": "PENDING_REVIEW",
        "current_version": current.version if current else None,
        "superseded_versions": [item.version for item in published_versions],
    }, new={"status": "PUBLISHED", "version": procedure.version, "review_note": review_note}, reason=review_note, at=at)
    s.notify(event, [procedure.created_by], "SAFETY_REVIEW_RESULT",
             f"你提交的场地安全信息v{procedure.version}已发布。复核意见：{review_note}", at)
    if procedure.requires_acknowledgement:
        upcoming = Reservation.objects.filter(
            resource=resource, status="CONFIRMED", starts_at__gt=at,
        ).select_related("applicant").order_by("starts_at", "pk")
        for booking in upcoming:
            s.notify(event, [booking.applicant], "SAFETY_VERSION",
                     f"预约{booking.code}对应场地安全信息已更新至v{procedure.version}。预约时段未自动取消；实际入场前，请在场地详情阅读并确认当前版本。",
                     at)
    return procedure


@transaction.atomic
def return_for_revision(actor, procedure_id, review_note, at=None):
    at = at or timezone.now()
    reference = SafetyProcedure.objects.select_related("resource").get(pk=procedure_id)
    resource = Resource.objects.select_for_update().get(pk=reference.resource_id)
    procedure = SafetyProcedure.objects.select_for_update().select_related("created_by").get(pk=procedure_id)
    s.check_manager(actor, resource)
    s.require(resource.kind == "ROOM", "安全规程只可关联场地资源")
    s.require(actor.pk != procedure.created_by_id, "规程提交人与复核人必须分离")
    s.require(procedure.status == "PENDING_REVIEW", "只有待审核版本可以退回修订")
    review_note = str(review_note or "").strip()
    s.require(bool(review_note), "退回修订必须写明具体原因和需补充的内容")
    s.require(len(review_note) <= 2000, "复核意见不得超过2000个字符")
    procedure.status = "RETURNED"
    procedure.reviewed_by = actor
    procedure.reviewed_at = at
    procedure.review_note = review_note
    procedure.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    event = s.audit(actor, procedure, "SAFETY_RETURN_REVISION",
                    old={"status": "PENDING_REVIEW"},
                    new={"status": "RETURNED", "version": procedure.version, "review_note": review_note},
                    reason=review_note, at=at)
    s.notify(event, [procedure.created_by], "SAFETY_REVIEW_RESULT",
             f"你提交的场地安全信息v{procedure.version}已退回修订。复核意见：{review_note}", at)
    return procedure, event


@transaction.atomic
def start_revision(actor, procedure_id, at=None):
    """Create an editable new draft from a returned, immutable review snapshot."""
    at = at or timezone.now()
    reference = SafetyProcedure.objects.select_related("resource").get(pk=procedure_id)
    resource = Resource.objects.select_for_update().get(pk=reference.resource_id)
    source = SafetyProcedure.objects.select_for_update().get(pk=procedure_id)
    s.check_manager(actor, resource)
    s.require(resource.kind == "ROOM", "安全规程只可关联场地资源")
    s.require(source.status == "RETURNED", "只有已退回修订的版本可以创建新草稿")
    s.require(source.created_by_id == actor.pk, "只有原提交人可以基于退回意见创建修订草稿")
    values = {name: getattr(source, name) for name in (
        "title", *CONTENT_FIELDS, "source_title", "source_reference", "source_version",
        "source_effective_date", "effective_from", "review_due_date", "requires_acknowledgement")}
    version = (SafetyProcedure.objects.filter(resource=resource).order_by("-version").values_list("version", flat=True).first() or 0) + 1
    values["change_reason"] = f"根据安全信息v{source.version}退回意见修订；请补充具体修改内容。"
    draft = SafetyProcedure.objects.create(resource=resource, version=version, created_by=actor, **values)
    s.audit(actor, draft, "SAFETY_REVISION_DRAFT_CREATE",
            new={"status": "DRAFT", "version": version, "based_on_version": source.version},
            reason=source.review_note, at=at)
    return draft


@transaction.atomic
def acknowledge(user, procedure_id, at=None):
    at = at or timezone.now()
    procedure = SafetyProcedure.objects.select_for_update().select_related("resource").get(pk=procedure_id)
    s.require(user.is_active and user.has_role("STUDENT", "TEACHER"), "当前身份不能确认预约安全信息")
    current = current_procedure(procedure.resource)
    s.require(procedure.status == "PUBLISHED" and procedure.requires_acknowledgement
              and current is not None and current.pk == procedure.pk,
              "此版本已过复核期限、已被替代或不要求阅读确认，不能作为当前版本确认")
    item, created = SafetyAcknowledgement.objects.get_or_create(user=user, procedure=procedure, defaults={"acknowledged_at": at})
    if created:
        s.audit(user, procedure, "SAFETY_READ_ACK", new={"user_id": user.pk, "version": procedure.version, "acknowledged_at": at}, reason="用户确认已阅读本版本；不代表培训、考核或准入", at=at)
    return item
