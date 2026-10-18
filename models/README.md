# 模型源文件

- `schema.json`：由Django实际模型导出的31个对象、完整字段、约束、索引和52条物理外键。
- `campus_lab.dbml`：可导入支持DBML的建模工具；组合、条件唯一、区间排斥及跨表规则以迁移与数据设计说明为准。
- `campus_lab.mmd`：完整Mermaid ERD，适合关系复核；完整大图不应缩小后直接作为A4打印图。
- `图形源文件/*.drawio`：用diagrams.net打开，可修改UML图和各业务域ERD。
- `图形源文件/*.svg`：独立矢量预览；本地需有中文字体。正式PDF已内嵌字体。

`AuditEvent.entity_type + entity_id`为明确的逻辑对象引用，未伪造为物理外键。Django会话、框架权限组和迁移基础表不属于本领域ERD；User继承的兼容列在正式字典中解释。MockReceipt是模拟消息适配器辅助记录。

重新导出字段：使用已安装依赖的Python运行 `python tools/export_model.py`。图形的领域分组与完整字段字典配套阅读。
