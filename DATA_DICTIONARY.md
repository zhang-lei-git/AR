# 数据字典

| 数据域 | 主表 | 主键或关联字段 | 主要内容 |
|---|---|---|---|
| AR终端 | `devices` | `id` | 状态、电量、能力、配置、心跳和适配器版本 |
| 工艺与内容 | `process_versions`, `process_steps` | `version`, `step_no` | 工艺版本、校验值、主要工序和多媒体内容 |
| 装配工单 | `tasks`, `step_executions` | `task_id`, `step_no` | 产品、型号、工位、人员、终端、工艺快照和执行状态 |
| 现场数据 | `field_observations` | `task_id`, `step_no`, `device_id` | 结构件扫码、照片、语音和测量数据 |
| 零部件识别 | `part_identifications` | `task_id`, `step_no` | 原始编码、零部件、型号、BOM条目、型号/工单/工序/BOM校验项、确认或复核或拦截结果 |
| 异常处置 | `incidents` | `id`, `task_id` | 类型、描述、责任人、状态、处置和关闭时间 |
| 知识资料 | `knowledge_assets` | `id`, `category`, `tags` | 文件名、类型、大小、分类、标签、适用范围、版本和RAG索引状态 |
| 智能知识 | `knowledge`, `ai_interactions` | `id`, `task_id` | 资料版本、证据段、问答、引用、拒答、模型和时延 |
| 维修知识 | `maintenance_cases` | `id`, `source_task` | 设备、部件、现象、原因、处置方案、备件和审核状态 |
| 维修系统集成 | `maintenanceIntegration` | `equipment.id`, `work_order.id` | 维修管理系统、设备档案、维修工单、故障现象、接口状态和结果回写说明 |
| 数字孪生集成 | `digitalTwin` | `model_id`, `twin_object_id` | 产线模型版本、工位对象、实时参数、告警、数据接口和AR展示内容 |
| 远程协作 | `collaboration_sessions`, `annotations` | `session_id` | 专家、会话、冻屏标注、资料发送、处置意见和录像索引 |
| 追溯审计 | `events`, `audits` | `task_id` | 业务事件、操作者、来源、接口、角色和结果 |
| 现场录像 | `recordings`（业务快照） | `task_id`, `step_no`, `device_id`, `operator` | AR现场录像地址、封面、时长、工单、工序、终端和人员关联 |
| 接口扩展 | `plugins` | `id` | 适配组件版本、入口、启停状态和说明 |

时间字段使用带时区的 ISO 8601 格式，接口使用 UTF-8 JSON，业务事件以装配工单编号统一关联。
