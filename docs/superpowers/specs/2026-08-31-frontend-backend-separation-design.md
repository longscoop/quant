# 前后端分离与个股页组合入口收敛设计

## 目标

将当前直接在 Streamlit 页面中调用 PostgreSQL Store 和领域工作流的研究工作台，渐进迁移为独立部署的 React + TypeScript 前端与 FastAPI 后端。后端继续复用 `quant` 中已验证的 PIT、因子、模型、组合账本和回测领域逻辑；任何研究结果、运行状态和错误状态均必须来自真实持久化数据或领域服务。

同时，移除个股详情中重复的“我的研究组合”卡片。个股详情不再写入组合、编辑目标权重或跳转组合页面；研究首页、候选池和独立“我的组合”页仍是组合操作入口。

## 已确认的产品范围

- 前端为 React + TypeScript 单页应用；后端为 FastAPI。
- 采用渐进迁移：先建立 API 和新前端，再按页面迁移；现有 Streamlit 暂时保留为兼容入口。
- 公共研究页面覆盖研究首页、候选池、个股详情、行业观察、我的组合、历史验证和数据状态；管理员页面仅在服务器明确启用管理员模式时暴露。
- 新旧界面读取同一 PostgreSQL 数据库，并调用相同的领域服务。不得引入第二套因子、模型、组合或回测计算。
- 不包含登录、权限模型重构、券商接入、实盘交易、港股扩展或数据源变更。

## 当前事实与迁移约束

当前入口 `streamlit_app.py` 直接构造 `PostgresStore`，并调用 `quant/research_ui.py`、`quant/portfolio_ui.py`、`quant/public_validation_ui.py`、`quant/public_status_ui.py` 和管理员 UI。页面投影还分别使用 `quant/insights.py`、`quant/workflows.py` 与 `quant/presentation.py`。

`quant/workflows.py` 是组合账本、调仓、净值和组合回放等状态改变操作的既有业务入口。API 只能调用这些服务，不能在路由函数中重新实现交易日判断、T+1 开盘成交、费用、持仓、PIT 或回测算法。

PIT 与回测口径保持不变：历史信号只使用当时可见的数据，默认以 T 日收盘生成信号、T+1 下一交易日开盘成交、每日收盘估值；不能用缺失的开盘价替换为收盘价，不能以最新数据填充历史结果。

## 方案选择

### 方案 A：完整替换后一次发布

先完成所有 API 与 React 页面，再移除 Streamlit。该方案最终形态干净，但发布周期长，且问题难以定位与回滚。

### 方案 B：渐进迁移（采用）

先交付 API 契约与一条可端到端验证的页面链路，再逐页替换。Streamlit 与 React 在迁移期间同读同一数据库，领域服务只有一份。每个迁移页面可以独立验收与回退，适合当前已有较多真实研究流程的系统。

### 方案 C：保留 Streamlit，仅抽取 Python UI 服务

这能降低 `streamlit_app.py` 的耦合，但不会形成独立可部署前端，无法满足前后端分离目标。

## 目标架构

```text
React + TypeScript SPA
        │ HTTPS / JSON
        ▼
FastAPI API（输入验证、授权边界、DTO 投影、错误映射）
        ▼
quant 应用服务（workflows / insights / presentation）
        ▼
PostgresStore ── PostgreSQL
```

### 后端目录与职责

- `backend/app/main.py`：FastAPI 应用、CORS、路由注册、生命周期管理。
- `backend/app/dependencies.py`：由 `DATABASE_URL` 创建和提供 `PostgresStore`；请求中不缓存会变化的业务结果。
- `backend/app/schemas/`：Pydantic 请求/响应 DTO。对 `date`、数值、状态和可空字段显式建模。
- `backend/app/routers/`：按资源组织的 HTTP 适配层，例如 `research`、`portfolio`、`runs`、`admin`。仅做参数校验、调用应用服务、DTO 映射和状态码选择。
- `backend/app/services/`：仅放为 API 提供的组合读取投影和请求编排；领域计算仍位于 `quant`，不复制。

API 路由不可直接查询 SQL 或自行拼装历史结果。需要复用的纯展示投影从 `*_ui.py` 下沉到无 Streamlit 依赖的 `quant` 投影模块，并由 API 和兼容 Streamlit 共同调用。

### 前端目录与职责

- `frontend/src/api/`：按资源封装 HTTP 调用和运行时响应校验。
- `frontend/src/features/`：以研究首页、候选池、个股详情、行业、组合、历史验证、数据状态和管理员为边界的页面功能。
- `frontend/src/components/`：无业务副作用的展示组件。
- `frontend/src/routes/`：前端路由与管理员页面可见性控制。
- `frontend/src/types/`：前端专用 DTO 类型；不得把数据库行结构当作前端模型。

前端不计算因子分、预测、组合净值、回测指标或状态推断；它只渲染 API 的明示值和明示状态。缺失数值展示 `--` 或“不可用”，不可转换为 `0`。

## API 契约与状态规则

第一阶段按页面读取需求提供版本化的 `/api/v1` JSON API。

- `GET /api/v1/research/home`：数据新鲜度与优先研究候选。
- `GET /api/v1/research/candidates`：候选池、筛选项和分页结果。
- `GET /api/v1/research/stocks/{ts_code}`：个股研究摘要、因子、行情/基准对比、估值走势、财务披露和证据组。
- `GET /api/v1/research/industries`：行业观察投影。
- `GET /api/v1/portfolios`、`GET /api/v1/portfolios/{portfolio_id}`：组合列表与组合仪表盘。
- `POST /api/v1/portfolios`、`POST /api/v1/portfolios/{portfolio_id}/targets`、资金流与组合生命周期端点：只调用现有组合工作流，返回真实的版本、订单与状态。
- `GET /api/v1/backtests` 与 `GET /api/v1/data-status`：研究运行、历史验证与数据状态。
- `/api/v1/admin/*`：仅在管理员模式启用时注册；不得向公共前端暴露内部运行编号、底层异常或敏感诊断。

响应使用稳定页面 DTO，而不是透传 Store 原始字典。每个可计算状态显式返回 `status`、`reason` 和必要的覆盖率；`COMPLETED`、`PARTIAL`、`INSUFFICIENT_DATA`、`NOT_TRAINABLE`、`FAILED` 等语义与领域结果一致。无结果不是成功空值，必须能让前端展示下一步或限制原因。

写入端点使用请求 DTO 校验证券代码、日期、权重和金额。领域服务拒绝请求时，API 以明确的客户端错误返回原因；意外异常经脱敏后返回通用错误，不返回栈追踪、数据库 URL、令牌或内部运行编号。

## 个股详情组合入口收敛

删除 `quant/research_ui.py` 中仅为个股详情使用的 `_portfolio_position`、`_save_portfolio_position` 和 `_render_detail_portfolio_controls`，并移除详情布局中的“我的研究组合”列。详情页面只保留研究摘要、因子、走势、估值、财务和证据。

“加入研究组合”仍保留在研究首页和候选池，且继续使用当前选中组合。组合权重编辑、归一化、保存版本、调仓和回放仅保留在独立“我的组合”页面。React 个股详情同样不得新增组合写入控件。

## 迁移阶段

1. **基础与 API 契约**：新增 FastAPI 工程、健康检查、数据库依赖、通用错误映射、研究首页和候选池只读 API；为纯投影建立单元测试和契约测试。
2. **研究阅读链路**：迁移个股详情、行业、数据状态和历史验证的读取 API 与 React 页面。此阶段移除 Streamlit 个股详情的重复组合卡片。
3. **组合工作台**：迁移组合读取和写入 API，以及 React 的组合页面；所有写入复用 `quant.workflows`。
4. **管理员工作台**：迁移显式管理员模式下的任务控制与审计展示，严格保留公开实例的隐藏边界。
5. **切换与退役**：当所有页面、写入链路与回归测试通过后，将部署入口切至 FastAPI + 静态前端；随后移除 Streamlit 依赖和兼容 UI，不删除领域逻辑或历史数据。

每阶段均可独立部署。前一阶段成功不意味着后续页面数据可用；页面必须按 API 实际状态显示。

## 测试与验收

- API 单元测试使用 `InMemoryStore` 或确定性 fixture；不得访问真实 Tushare。
- API 集成测试验证输入校验、DTO 空值语义、公共/管理员路由边界和脱敏错误。
- React 组件与页面测试覆盖 API 的 `COMPLETED`、`PARTIAL`、`INSUFFICIENT_DATA`、`NOT_TRAINABLE` 和失败状态。
- 契约测试验证前端消费的 JSON 与后端 DTO 一致，并覆盖日期、空值、数值、分页和错误码。
- 组合写入与回放回归测试继续覆盖 PIT、历史股票池、T/T+1 成交边界、交易成本、不可训练状态，以及 FACTOR 与 MODEL 路径独立性。
- Streamlit 兼容页面测试在退役前继续通过；个股详情测试断言不再包含“我的研究组合”“加入研究组合”“目标权重”“保存目标权重”与“检查我的组合”。
- 前端端到端测试验证研究首页到候选池、候选池到个股详情，以及候选池加入组合后在独立组合页可见；个股详情不提供组合写入入口。

## 非目标与风险控制

- 本次不改变 PostgreSQL schema、研究结果、因子公式、模型训练、数据同步协议或组合账本口径，除非后续独立需求明确批准。
- 不提供 API 静默 fallback；依赖数据缺失时以明确状态和原因返回。
- 不在浏览器持久化敏感配置、数据库连接、Tushare Token 或管理员诊断。
- CORS 仅允许明确配置的前端来源；生产部署需分别配置前端公开 API 基地址和后端数据库环境变量。
