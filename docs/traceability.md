# 需求追踪矩阵

终稿 PDF 未找到；下列“现状问题”是本提示词描述的待解决场景，不是假称实地调研所得。FR/SPEC/BR/AC 均为本项目开发基线自编号；UC 保留用户指定的19项编号。取得终稿后需单独核对，未伪造原报告编号或追踪关系。

|现状问题（提示词场景）|改进措施|FR|UC / 名称|SPEC|BR / 规则|AC|实现位置|对应测试|
|---|---|---|---|---|---|---|---|---|
|设备时段与当前状态混淆|分离实物/服务/时段|FR-01|UC-01 查询设备可用状态|SPEC-01|BR-01 半开区间、当前借出、逾期阻塞|AC-01|services.available; views.equipment_detail|test_uc01_free_and_occupied; test_uc01_service_and_boundary|
|申请规则不一致|服务器集中校验|FR-02|UC-02 提交预约申请|SPEC-02|BR-02 时间、资格、逾期、额度、个人冲突|AC-02|services.reserve/validate_time/eligibility|test_uc02_submit_and_missing_equipment; test_time_validation_and_adjacent_personal_slots; test_personal_quota_independent_of_device_conflict|
|竞争审批可能重复占用|事务闸门及再次校验|FR-03|UC-03 审核预约|SPEC-03|BR-03 待审核不占设备，通过原子检查|AC-03|services.review; Review|test_uc03_approve_and_competitor; test_actual_parallel_approval_one_winner; test_parallel_maintenance_and_approval|
|取消/过期后占用未释放|受控转换+定时失效|FR-04|UC-04 取消预约|SPEC-04|BR-04 本人开始前取消，边界失效|AC-04|services.cancel/expire_locked; jobs.schedule|test_uc04_cancel_and_late_or_other; test_expiry_scheduler_repeated|
|实物借出核验不完整|身份标签配件功能核验|FR-05|UC-05 办理设备借出|SPEC-05|BR-05 已通过+领取窗口+唯一借出|AC-05|services.issue; IssueForm; Loan|test_uc05_issue_and_identity_duplicate; test_issue_window_and_service_block|
|归还与维修混淆|归还独立事实+原子异常|FR-06|UC-06 办理设备归还|SPEC-06|BR-06 实际接收、逾期、异常原子提交|AC-06|services.receive; ReturnRecord|test_uc06_normal_return_duplicate_missing_loan; test_damage_missing_fault_atomic_return; test_transaction_rollback_abnormal_return|
|异常未及时隔离|待确认即临时隔离|FR-07|UC-07 登记设备异常|SPEC-07|BR-07 取消未来申请，借出事实保留|AC-07|services.report_incident/confirm_incident|test_uc07_user_incident_isolation_and_unrelated_denied; test_uc07_confirm_false_alarm_restore_after_return|
|维修人员自行恢复风险|独立管理员验收|FR-08|UC-08 处理维修工单|SPEC-08|BR-08 本人工单，其他阻塞检查|AC-08|services.progress_work/accept_work/try_restore|test_uc08_full_repair_and_technician_cannot_accept; test_uc08_multiple_work_orders_and_incidents_block_restore|
|身份与权限边界不清|可替换模拟认证与会话|FR-09|UC-09 登录系统|SPEC-09|BR-09 哈希、CSRF、不绕过角色|AC-09|identity.MockCampusBackend; config.settings|test_login_csrf_and_password_hash; BrowserAcceptance.login|
|归属开放信息难查询|实验室与设备入口|FR-10|UC-10 查询实验室|SPEC-10|BR-10 不实现整间预约|AC-10|Laboratory; views.labs|test_pages_all_roles_and_form_errors|
|记录难检索/数据越权|范围过滤、筛选分页|FR-11|UC-11 查询预约与借用记录|SPEC-11|BR-11 本人/授权实验室|AC-11|queries.reservations_for/loans_for; views.record_filters|test_backend_scope_direct_id_and_post; test_pages_all_roles_and_form_errors|
|用户异常没有闭环|相关设备说明与核验|FR-12|UC-12 提交设备异常说明|SPEC-12|BR-12 本人借用过，形成待确认|AC-12|services.report_incident; IncidentForm|test_uc07_user_incident_isolation_and_unrelated_denied|
|任务责任不明确|确认后创建并指派|FR-13|UC-13 创建维修工单|SPEC-13|BR-13 授权管理员+有效维修人员|AC-13|services.create_work_order; WorkCreateForm|test_uc08_full_repair_and_technician_cannot_accept; test_open_work_order_prevents_reservation_and_false_resolution|
|维护影响预约不可见|维护原子取消与通知|FR-14|UC-14 维护设备信息|SPEC-14|BR-14 授权实验室、安全资格、不跳过恢复|AC-14|services.maintain_equipment/add_equipment|test_equipment_creation_and_safety_metadata; test_maintenance_during_review|
|系统与业务管理员混同|单一显式角色+实验室范围|FR-15|UC-15 管理用户与角色|SPEC-15|BR-15 系统身份不授予业务审核|AC-15|User; services.save_user; views.users|test_user_role_assignment_scopes_and_password; test_uc03_reject_reason_and_wrong_scope|
|业务参数不可追踪|配置、实验室、资格维护|FR-16|UC-16 维护基础数据|SPEC-16|BR-16 正参数、可配置课程假设|AC-16|BusinessConfig; services.save_config/save_lab/save_qualification|test_lab_qualification_parameters|
|状态变更责任难追溯|同事务只读审计|FR-17|UC-17 查看操作日志|SPEC-17|BR-17 操作者、时间、原因、追踪、禁止修改删除|AC-17|Audit; services.audit; migration 0002; views.audits|test_database_constraints_and_audit_immutable; test_transaction_rollback_submit_and_review|
|消息失败影响业务|outbox、租约、重试、模拟服务|FR-18|UC-18 发送消息通知|SPEC-18|BR-18 首次+3次重试、事件唯一、事务外发送|AC-18|Notification/MessageDelivery; jobs; worker command|test_notification_failure_does_not_rollback_business_and_retry_recovery; test_notification_final_failure_and_stale_lease; test_due_overdue_notifications_once|
|统计口径不明确|扣除服务中断的实际利用率|FR-19|UC-19 查看利用率报表|SPEC-19|BR-19 周期裁剪、分子分母及范围|AC-19|ServiceInterval; queries.utilisation; views.report|test_report_downtime_excluded; test_pages_all_roles_and_form_errors|

横向保障：`command` 事务/幂等由 test_request_idempotency_and_misuse、test_issue_and_return_idempotency、test_parallel_idempotent_submit、test_parallel_personal_quota、test_atomic_rollback_cancel_issue_acceptance 覆盖；实际页面三流程由 `core/tests/test_browser.py` 覆盖；恢复和响应目标由 `scripts/backup.py`、`scripts/measure.py` 与 artifacts 记录。最终真实执行状态见 acceptance，不把追踪关系本身当作测试通过证据。
