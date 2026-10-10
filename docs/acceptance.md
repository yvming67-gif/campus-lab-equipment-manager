# 验收记录

执行日期：2026-10-10，界面时区 Asia/Shanghai。环境：Windows、Python 3.13.12、Django 5.2.18、PostgreSQL 17.11、psycopg 3.3.6、agent-browser 0.27.2、本机 Chrome。指定终稿尚未核对，以下 AC 编号来自本提示词开发基线。

## 给定—当—那么

|编号|给定—当—那么|自动化位置|
|---|---|---|
|AC-01 / UC-01|给定正常设备；当查询空闲/批准占用/首尾相接/停用时段；那么分别空闲/冲突/空闲/阻塞|BusinessTests.test_uc01_free_and_occupied、test_uc01_service_and_boundary|
|AC-02 / UC-02|给定合格且无逾期申请人；当提交正确预约；那么待审核；给定设备缺失、时长/粒度/额度/个人冲突错误；那么明确拒绝且无部分记录|test_uc02_submit_and_missing_equipment、test_time_validation_and_adjacent_personal_slots、test_personal_quota_independent_of_device_conflict|
|AC-03 / UC-03|给定竞争待审核申请；当管理员批准；那么原子再次检查；两个独立事务同时批准最多一个通过；系统管理员或无实验室授权不能批准|test_uc03_approve_and_competitor、test_uc03_reject_reason_and_wrong_scope、ConcurrencyTests.test_actual_parallel_approval_one_winner|
|AC-04 / UC-04|给定本人未开始申请；当有原因取消；那么释放占用；他人、开始后、终态取消拒绝|test_uc04_cancel_and_late_or_other|
|AC-05 / UC-05|给定批准预约和领取窗口；当身份/标签/配件/功能正常且无阻塞；那么唯一借出；身份标签错误、未核配件、重复或窗口外拒绝|test_uc05_issue_and_identity_duplicate、test_issue_window_and_service_block|
|AC-06 / UC-06|给定未结束借出；当正常/逾期/损坏/缺件/故障归还；那么保存真实接收和检查，异常原子隔离，逾期秒数按实际时间；无借出和重复拒绝|test_uc06_normal_return_duplicate_missing_loan、test_overdue_user_and_device_availability、test_damage_missing_fault_atomic_return|
|AC-07 / UC-07|给定相关借用设备；当用户登记异常；那么待确认、临时隔离、取消未来受影响预约且保留借出事实；无关用户拒绝|test_uc07_user_incident_isolation_and_unrelated_denied、test_uc07_confirm_false_alarm_restore_after_return|
|AC-08 / UC-08|给定本人指派工单；当维修提交且管理员验收；那么关闭并检查恢复；维修人员自验收/他人工单拒绝，多异常多工单或停用阻止恢复|test_uc08_full_repair_and_technician_cannot_accept、test_uc08_multiple_work_orders_and_incidents_block_restore、test_disabled_blocks_repair_restore_and_acceptance_rejection|
|AC-09 / UC-09|给定本地账号；当正确登录；那么角色首页；密码哈希、登录 CSRF 有效，未登录重定向|WebTests.test_login_csrf_and_password_hash、浏览器多角色登录|
|AC-10 / UC-10|给定三个模拟实验室；当按名称/位置查询；那么展示开放与设备入口|WebTests.test_pages_all_roles_and_form_errors、浏览器设备目录|
|AC-11 / UC-11|给定本人/授权实验室记录；当查询、筛选分页或构造其他编号；那么仅返回范围内记录，越权404|WebTests.test_backend_scope_direct_id_and_post|
|AC-12 / UC-12|给定本人借用过设备；当提交相关说明；那么形成待确认异常；他人设备拒绝|test_uc07_user_incident_isolation_and_unrelated_denied|
|AC-13 / UC-13|给定已确认异常；当管理员指派有效维修人员；那么创建工单并通知；未确认/无权创建拒绝|test_uc08_full_repair_and_technician_cannot_accept、AdministrationTests.test_open_work_order_prevents_reservation_and_false_resolution|
|AC-14 / UC-14|给定授权实验室；当新增或维护设备及安全要求；那么持久化并取消不能履约的未来申请；其他实验室拒绝|AdministrationTests.test_equipment_creation_and_safety_metadata|
|AC-15 / UC-15|给定系统管理员；当创建用户、改角色及授权；那么密码哈希和范围生效；普通管理员不能管理账号|AdministrationTests.test_user_role_assignment_scopes_and_password|
|AC-16 / UC-16|给定系统管理员；当维护实验室、培训资格及参数；那么规则读取配置；无权/非法参数拒绝|AdministrationTests.test_lab_qualification_parameters|
|AC-17 / UC-17|给定关键状态变化；当查询日志或尝试更新删除；那么有同事务审计且篡改拒绝|test_database_constraints_and_audit_immutable、事务故障注入|
|AC-18 / UC-18|给定成功业务、模拟消息失败；当发送/重试/恢复/重复调度；那么业务保留、间隔1/5/15分钟、至多3次重试、不重复提醒|test_notification_failure_does_not_rollback_business_and_retry_recovery、test_notification_final_failure_and_stale_lease、test_due_overdue_notifications_once|
|AC-19 / UC-19|给定实际借出及停用区间；当管理员查询统计；那么分子分母扣除服务中断，权限范围有效|test_report_downtime_excluded、WebTests.test_pages_all_roles_and_form_errors|

## 执行结果

- 全量 **52项通过，106.780秒**，包括业务、权限、管理、事务故障注入、4项真实 PostgreSQL 并发和3条真实 Chromium 流程。执行：`python manage.py test core.tests --noinput --verbosity 2`；原始输出见 `artifacts/test-results.txt`。
- 并发：真实 PostgreSQL，独立 backend PID，屏障同时启动审核/个人额度/同请求重试/维护竞争四项均通过。没有 SQLite 替代。
- 三条真实浏览器流程：**全部通过**。正常预约审核借还；异常归还、建单、维修处理、管理员验收恢复；两申请人竞争、两个浏览器会话同时审核，仅一个通过。使用实际 Chromium 与 HTTP 表单，业务时钟注入在测试进程，没有生产测试接口。证据在 `artifacts/browser/`；日期控件采用浏览器 DOM 填值、提交按钮先滚动进入视区。
- 备份恢复：已通过，独立新库，26张表、368行，全部表计数及 SHA256 行摘要一致，恢复验证1.069秒；见 `artifacts/restore-verification.json`。此记录对应备份时刻，后续负载工具会新增限定模拟历史。
- 负载：15台设备、3个实验室，测量结束24条预约/24用户（含工具模拟用户）；8并发120次查询，P95 **499.59ms**，最大571.69ms；4并发16次真实成功预约提交，P95 **496.5ms**；提交后重新查询最长 **244.93ms**。阈值分别3秒/5秒/5秒，**本环境达标**。见 `artifacts/performance.json`。
- 服务运行：本机应用 http://127.0.0.1:8000 和独立 worker 已启动，真实数据库迁移及种子执行成功。
- 持久化：按 README 再次运行 setup 幂等初始化，交付的 stop/start 脚本实际重启应用、worker 及 PostgreSQL 后，七类核心业务对象数量和 SHA256 数据摘要一致，浏览器设备列表重新查询成功。见 `artifacts/persistence.json`。依赖检查及全项目 Python 编译通过。
- 界面：真实浏览器管理员首页与设备列表的可访问性树和 DOM 边界检查，1258px视口无页面横向溢出，导航/标题无内部文字溢出；不是像素级视觉识别验收。外部识图调用被自动审批拒绝，没有发送截图。
- Docker Compose：配置已提供，本机没有 Docker，**本机未验证**。Windows 手动路径是此次运行和验收路径。
- GitHub/CI：第一阶段完成前未创建仓库、未推送；远程状态待第二阶段记录，不以本地配置代替远程运行结果。

## 质量限制

负载为课程规模的 loopback HTTP，未包括真实校园网络、长期运行、大量设备或大规模并发。Windows 便携版本初始化需 ASCII junction；数据库所有者属于可信运维边界。模拟外部消息没有真实通信平台。RPO24h/RTO4h 尚需每日调度、异机备份、凭据管理和故障环境准备；已验证小数据恢复，不宣称完成长期运维保障。没有真实学校制度或实施效果数据。
