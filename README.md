# 校园实验室设备预约与借用管理系统

`campus-lab-equipment-manager` ·《系统分析与设计》课程演示 · 某高校实验教学中心（模拟组织）。

本系统管理带唯一标签的单台或整套设备，覆盖查询 → 预约 → 人工审核 → 借出 → 检查归还 → 异常维修 → 管理员验收。所有账号、实验室、设备、培训及消息服务均为模拟数据；没有连接真实学校统一认证、短信或邮件平台。本期只在本地运行。

## 技术与目录

Python **3.13.12**、Django **5.2.18 LTS**、PostgreSQL **17.11**、psycopg **3.3.6**。依赖及传递依赖版本见 `requirements.txt`。使用 Django 模板、统一 CSS、少量 JavaScript；没有 Redis 或 Celery。

官方兼容性与安全依据：[Django 5.2](https://docs.djangoproject.com/en/5.2/releases/5.2/)、[PostgreSQL 支持](https://docs.djangoproject.com/en/5.2/ref/databases/)、[5.2.18 安全公告](https://www.djangoproject.com/weblog/2026/oct/06/security-releases/)、[Windows PostgreSQL 官方下载入口](https://www.postgresql.org/download/windows/)。

- `core/models.py`：数据对象、约束、索引。
- `core/services.py`：角色/实验室权限、规则、状态转换、事务与幂等。
- `core/queries.py`：数据范围与报表口径；`core/jobs.py`：任务表及模拟发送。
- `core/views.py`、`templates/`、`static/`：各角色的中文业务页面。
- `core/migrations/`、`core/tests/`、`scripts/`、`docs/`：迁移、测试、启动及交付资料。

## Windows 安装与启动

准备：安装 [Python 3.13 Windows 版](https://www.python.org/downloads/windows/)（勾选 Python Launcher），联网下载 Python 包及 PostgreSQL 官方便携包。浏览器验证还需 [Node.js 22](https://nodejs.org/en/download)。安装工具本身可能需要 Windows 许可；项目运行无需注册数据库系统服务。

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

访问 **http://127.0.0.1:8000**。初始化创建 `.venv`、随机本地配置、项目专用 PostgreSQL、执行迁移、写入模拟数据。数据库监听 `127.0.0.1:55432`；应用监听 `127.0.0.1:8000`。后台工作进程每15秒处理失效与通知。此配置为本地课程演示，使用 Django 开发服务器。

中文路径上的 EDB 初始化会发生字符编码问题，`local_db.py` 自动在当前用户临时目录建立 ASCII junction 指向项目，数据库实物文件仍在 `.local/pgdata`。不要删除该运行别名；移动项目后重新运行初始化工具即可生成新别名。

也可分别在两个终端手动运行：

```powershell
.venv/Scripts/python scripts/local_db.py init
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py seed_demo
.venv/Scripts/python manage.py runserver 127.0.0.1:8000 --noreload
# 第二个终端
.venv/Scripts/python manage.py worker
```

停止 `start.ps1` 启动的应用：`powershell -ExecutionPolicy Bypass -File scripts/stop.ps1`。数据库停止：`.venv/Scripts/python scripts/local_db.py stop`。不用脚本启动的终端服务使用 Ctrl+C。日志位于 `.local/`。

已有 PostgreSQL 时，设置进程环境变量 `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD/SECRET_KEY`，或修改未入库的 `.local/settings.json`；格式见 `config.example.json`。环境变量优先。本地测试数据库账号需要创建测试库权限；便携演示实例使用独立本地管理账号，不能直接作为公网生产配置。

## 演示账号

初始化生成随机密码，安全哈希存入用户表。明文演示凭据只保存在本地 **`.local/demo-accounts.json`**，不进入 Git。

| 账号 | 角色 |
|---|---|
| demo_student1 / demo_student2 / demo_student3 | 学生；student1 有培训资格；student3 含模拟逾期案例 |
| demo_teacher1 | 教师，有模拟培训资格，仍需审批 |
| demo_labadmin / demo_labadmin2 | 实验室管理员，授权三个模拟实验室 |
| demo_repair1 | 维修人员，仅处理本人指派工单 |
| demo_sysadmin | 系统管理员，管理用户、角色、实验室、参数、资格与审计 |

密码文件丢失时保留原账号密码，可运行 `.venv/Scripts/python manage.py changepassword demo_student1` 在本地交互重置，不在聊天或 Git 中填写密码。`seed_demo` 可重复执行，保护非模拟数据；`reset_demo` 通过业务服务收尾限定范围的模拟案例并保留历史；`seed_demo --refresh-cases` 追加相对当前时间的新案例。重置跳过涉及非模拟用户/业务的设备。

## Docker Compose 便捷启动

先安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)（WSL2 后端），确保 8000 端口空闲，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compose.ps1 up
docker compose exec app cat .local/demo-accounts.json
powershell -ExecutionPolicy Bypass -File scripts/compose.ps1 logs
powershell -ExecutionPolicy Bypass -File scripts/compose.ps1 down
```

应用、数据库、worker 三服务；数据库健康后迁移/初始化，应用健康后启动 worker。数据库与演示凭据使用命名卷，`down` 保留数据。Linux 用户可在进程环境中设置随机 `PGPASSWORD` 和 `SECRET_KEY` 后执行 `docker compose up --build -d`。本机未安装 Docker，Compose 本地运行状态见验收文档，不能把配置存在当作已运行。

## 测试与验收

```powershell
.venv/Scripts/python manage.py check
.venv/Scripts/python manage.py makemigrations --check --dry-run
.venv/Scripts/python manage.py test core.tests.test_business core.tests.test_administration core.tests.test_concurrency --verbosity 2
npm install -g agent-browser@0.27.2
agent-browser install
.venv/Scripts/python manage.py test core.tests.test_browser --verbosity 2
# 全部自动化（需要浏览器依赖）
.venv/Scripts/python manage.py test core.tests --verbosity 2
# 应用已启动时的实际 HTTP 负载验证
.venv/Scripts/python scripts/measure.py
```

所有业务/并发测试使用真实 PostgreSQL；并发测试为独立连接与同时启动的事务。浏览器测试在隔离测试数据库中使用实际 Chromium、多角色会话及可控业务时钟；日期控件通过浏览器 DOM 填值，所有状态转换仍由页面表单提交。没有公开的测试时钟接口。`test_campus_lab` 是 Django 专用测试库，不要用此名称存储真实数据。

`measure.py` 使用 8 并发查询/4 并发提交，创建限定模拟用户和真实预约，结束后取消它自己的待审核预约并停用测试用户，保留审计。具体结果及限制见 [验收记录](docs/acceptance.md) 和 `artifacts/`。GitHub Actions 使用 PostgreSQL 17.11 和真实 Chromium，远程真实状态以 Actions 页面为准。

## 备份、恢复与后台任务

```powershell
.venv/Scripts/python scripts/backup.py create
.venv/Scripts/python scripts/backup.py verify
.venv/Scripts/python manage.py worker --once --mode fail
.venv/Scripts/python manage.py worker --once --mode success
# 可控时间仅用于单次后台任务，必须带时区
.venv/Scripts/python manage.py worker --once --at 2026-10-11T10:30:00+08:00
```

备份位于 `.local/backups/`，使用导出事务快照执行 `pg_dump -Fc`，同时保存全表数量及 SHA256 行摘要。`verify` 新建随机命名隔离库，执行 `pg_restore`、核对全部数据，随后只删除它自己创建的验证库。它不覆盖运行库。实际验证结果见 `artifacts/restore-verification.json`。

需要保留恢复库时，使用 PostgreSQL 的 `createdb` 新建独立目标库，再执行 `pg_restore --no-owner --no-acl --exit-on-error -d <新库> <备份.dump>`，通过进程环境变量提供数据库密码；不要把密码写进命令或文档。验证后再修改本地 `PGDATABASE` 指向恢复库。原库始终保留。

初步运维目标：RPO 24小时、RTO 4小时。需 Windows 任务计划每天成功执行备份、保存异机副本、监测失败、准备可用 PostgreSQL/Python 和凭据、定期演练。本次验证的是本机小数据恢复，不代表已经配置持续备份或具备故障环境的四小时恢复保证。

通知先入库，事务外发送。模拟模式 `fail/success` 演示故障与恢复；站内通知即时可查。首次失败后1、5、15分钟重试，共最多4次发送尝试。恢复时仍须等待任务到期。独立 worker 默认模式来自 `SIMULATED_MESSAGE_MODE`，不要同时用默认成功 worker 演示强制失败。

## 文档与常见问题

- [需求基线](docs/requirements.md)、[架构与状态机](docs/architecture.md)、[验收](docs/acceptance.md)、[追踪矩阵](docs/traceability.md)、[演示步骤](docs/demo.md)。
- 不能预约：查看设备服务状态、所需资格、本人逾期、个人时间冲突与未来数量上限。待审核不占设备，但占个人时间与额度。
- 不能借出：须在开始时间至30分钟领取宽限内到场，已批准、身份标签匹配，设备没有未结束借出或其他阻塞。
- 维修验收后未恢复：检查其他异常、其他开放工单、停用/报废状态和未归还借出。
- 预约显示失效：待审核到了开始时间，或已通过超过领取宽限。worker 应保持运行。
- 服务不能启动：先查看 `.local/server-error.log`、PostgreSQL 状态和端口；初始化下载需要网络。不要删除已有数据库来解决端口问题。
- 报告缺失：指定终稿没有找到，当前编号与假设来自本提示词；附近课程教材没有被当作该报告。

## 交付范围与限制

没有支付、采购、赔偿、整间实验室预约或真实校内平台接口。业务写操作以单个 PostgreSQL 配置行作为事务锁，保证课程演示规模的一致性；大规模并发需要进一步拆分锁和压测。所有操作入口均在后端校验角色和实验室范围，系统管理员不会自动拥有借还审核权限。审计表具备禁止 UPDATE/DELETE 的数据库触发器。

本地工程和验收完成前不会创建远程仓库。公开托管目标为 `yvming67-gif/campus-lab-equipment-manager`；是否已创建、推送和 CI 通过，见最终交付及真实远程记录，不能由该目标名称推断。
