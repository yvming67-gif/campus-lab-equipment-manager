# 交付核验

时间：2026-10-10（Asia/Shanghai）。阶段顺序：本地实现及验收完成 → 本地 Git 提交 → GitHub 官方设备授权 → 核对实际账号 → 创建公开仓库 → 正常推送 → 远程文件与 CI 核验。未在本地完成前创建或推送远程仓库。

- 本地项目目录：`校园实验室设备预约与借用管理系统`。
- 启动：项目目录执行 `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`，访问 http://127.0.0.1:8000。
- 账号：本地 `.local/demo-accounts.json`，随机密码不进入 Git；账号说明见 README。
- 核心实现：UC-01—UC-19，预约、审核、借还、异常维修闭环，资格/逾期/范围权限、事务/幂等、通知后台、审计及统计。
- 本地自动化：52项通过，106.780秒，原始记录 `artifacts/test-results.txt`。三条真实 Chromium 流程和4项真实 PostgreSQL 并发均通过。
- 恢复验证：26张表368行，全部计数与 SHA256 数据摘要一致，1.069秒；具体快照时刻见 artifacts。
- 重启验证：PostgreSQL、应用和 worker 重启后七类业务对象数据摘要一致。
- 本机课程负载：查询8并发120次，P95 499.59ms；提交4并发16次，P95 496.5ms；重新查询最长244.93ms。
- GitHub 实际授权账号：`yvming67-gif`，通过官方设备授权登录；不收集聊天中的密码或令牌。
- 实际公开仓库：[yvming67-gif/campus-lab-equipment-manager](https://github.com/yvming67-gif/campus-lab-equipment-manager)。创建前已检查同名仓库不存在，未覆盖远程历史，未强制推送。
- 首个完整代码提交：`43c110f7a3d956e502ccd4d3a300104c342e761e`，本地和远程 main 一致。
- 首批远程文件：67个，密钥目录、虚拟环境、数据库文件/备份、日志、PDF/Word个人资料均未上传。`scripts/check_secrets.py` 对 Git 暂存内容扫描通过，并核验了实际远程树。
- 首次真实 CI：[运行 38054080467](https://github.com/yvming67-gif/campus-lab-equipment-manager/actions/runs/38054080467)，**success**；Ubuntu + PostgreSQL17.11 + Python3.13.12 + Chromium，含完整52项测试和浏览器证据上传。

交付文档补充提交也触发同一工作流；每个提交最新真实状态见 [Actions](https://github.com/yvming67-gif/campus-lab-equipment-manager/actions)。本文件固定记录首次已验证代码提交，避免将未执行的新运行标为通过。

限制：指定终稿未找到，基线来自提示词；本机未安装 Docker，Compose 本机未验证；未接入真实校内身份或消息服务；无公网部署。RPO24h/RTO4h 是依赖调度和环境准备的初步目标，未宣称已具备长期运维保障。暂无已知阻断核心流程的问题；真实学校开放日历、培训有效制度、预约参数和利用率口径仍需业务方确认。
