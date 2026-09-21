# 研究模拟组合账本设计

## 1. 目标

把现有“单一组合 + 当前目标权重 + 简化买入持有曲线”升级为可审计的多组合研究模拟系统。页面信息结构参考用户提供的组合页面截图，但所有持仓、成交、收益、风险、行业暴露、因子暴露和历史回放结果必须由 PostgreSQL 中的真实行情、PIT 因子快照、行业记录和持久化组合账本计算，禁止静态占位、假数据、假进度或静默 fallback。

组合仍是研究工具，不接入券商账户，不下实盘订单，不展示为真实账户持仓。

## 2. 已批准的产品口径

- 第一阶段只支持 A 股组合，覆盖系统已有的沪深300与创业板研究证券；港股不进入同一账本。
- 每个组合创建时填写初始模拟资金，默认 `1,000,000` 元。创建后初始资金不可直接改写，后续调整必须形成明确的资金流入或流出记录。
- 支持多个相互隔离的组合，可新建、复制、切换和删除。
- 保存目标权重后自动形成不可覆盖的目标版本和调仓计划，不再要求单独点击“执行模拟调仓”。
- 单边交易费率按组合配置，默认 `0.05%`。买入和卖出成交时均按成交金额扣费，实际费率与费用写入成交账本。
- 信号日为保存权重时数据库中最新可用的收盘日 `T`；成交只能使用 `T+1` 下一真实交易日的开盘价。
- 当 `T+1` 数据尚未同步时，计划保持待成交，不能回填到历史日期或使用 `T` 日价格成交。
- 买入数量按 100 股整数手向下取整；卖出数量不超过账本中的可用模拟股数。
- 成本价采用移动加权平均法。已实现收益来自卖出成交，未实现收益来自最新真实收盘估值。
- 未分配目标权重保留为现金，不自动归一化到 100%。
- 沪深300 `000300.SH` 是默认比较基准。

## 3. 范围

### 3.1 本期范围

- 多组合生命周期管理。
- 初始资金与显式资金流记录。
- 目标权重版本、调仓计划、模拟成交、现金和持仓账本。
- 保存权重后自动生成调仓计划。
- 新行情同步后自动处理待成交计划。
- 每日收盘估值、组合净值、基准净值和风险指标。
- 持仓、调仓、行业暴露、因子暴露、历史回放和诊断页面。
- 候选池和个股详情加入当前组合草案。
- 原有 FACTOR 与 MODEL 研究路径保持独立且不受组合功能影响。

### 3.2 不在本期范围

- 券商连接、真实账户同步、实盘订单或自动交易。
- 港股、跨币种现金、汇率和港股交易费用。
- 融资融券、卖空、杠杆、期货或期权。
- 日内成交、限价委托或成交量冲击模型。
- 用最新股票池、行业或因子数据回填历史。
- 猜测具体券商佣金、最低收费或税费；本期只使用明确配置的单边费率。

## 4. 核心架构

采用“事件账本 + 可重算投影”架构：用户意图和市场事件持久化为不可覆盖的记录，当前持仓、现金、成本、收益和页面汇总由这些记录投影得到。每日净值作为可重建的物化结果保存，以支持高效页面读取和审计。

数据链路为：

`组合目标版本 -> 调仓计划 -> T+1 真实开盘成交 -> 成交/现金账本 -> 每日收盘估值 -> 持仓、收益、暴露与页面`

历史回放与组合自创建以来的模拟实绩分开：

- 模拟实绩只使用创建后实际形成的目标版本和成交账本。
- 历史回放冻结一个选定的目标版本，在用户选择的历史区间按月度调仓规则独立运行，并保存为 `portfolio_backtest` 研究运行。

## 5. 领域类型

在 `quant/types.py` 增加明确类型，避免用无约束字典传递核心状态：

- `ResearchPortfolio`：组合基本信息、初始资金、基准、费率和状态。
- `PortfolioTargetRevision`：目标版本、信号日期、创建时间和备注。
- `PortfolioTargetItem`：版本内证券与目标权重。
- `PortfolioRebalanceOrder`：计划交易、目标数量、计划交易日和状态。
- `PortfolioTrade`：真实模拟成交、价格、数量、金额、费用和原因。
- `PortfolioCashFlow`：初始资金及显式资金流。
- `PortfolioHolding`：由账本投影得到的当前股数、成本和收益。
- `PortfolioDailyValuation`：每日现金、市值、总资产、净值、基准净值和覆盖状态。
- `PortfolioStatus`：`ACTIVE`、`ARCHIVED`。
- `PortfolioOrderStatus`：`PENDING`、`PARTIAL`、`COMPLETED`、`SUPERSEDED`、`FAILED`。
- `PortfolioValuationStatus`：`COMPLETED`、`PARTIAL`、`INSUFFICIENT_DATA`。

## 6. PostgreSQL 数据模型

### 6.1 `research_portfolios`

扩展现有表：

- `portfolio_id text primary key`
- `name text not null`
- `initial_capital numeric not null check (initial_capital > 0)`
- `benchmark_code text not null default '000300.SH'`
- `transaction_cost_bps numeric not null default 5 check (transaction_cost_bps >= 0)`
- `status text not null default 'ACTIVE'`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`

现有 `default` 组合在迁移时保留，并补充默认初始资金、基准和费率。迁移不能凭空生成历史成交；旧组合权重只迁移为一个未执行的目标草案，页面明确提示用户首次保存后才形成新账本计划。

### 6.2 `portfolio_target_revisions`

- `revision_id uuid primary key`
- `portfolio_id text not null references research_portfolios`
- `revision_no integer not null`
- `signal_date date not null`
- `status text not null`
- `note text`
- `created_at timestamptz not null default now()`
- `unique (portfolio_id, revision_no)`

### 6.3 `portfolio_target_items`

- `revision_id uuid not null references portfolio_target_revisions on delete cascade`
- `ts_code text not null references securities`
- `target_weight numeric not null check (target_weight >= 0 and target_weight <= 1)`
- `primary key (revision_id, ts_code)`

同一版本全部目标权重之和不得超过 `1.0`。该约束在领域服务中校验，并在保存版本的数据库事务内再次校验。

### 6.4 `portfolio_rebalance_orders`

- `order_id uuid primary key`
- `portfolio_id text not null references research_portfolios`
- `revision_id uuid not null references portfolio_target_revisions`
- `ts_code text not null references securities`
- `side text not null`
- `target_weight numeric not null check (target_weight >= 0 and target_weight <= 1)`
- `target_quantity numeric`
- `remaining_quantity numeric`
- `planned_trade_date date`
- `status text not null`
- `reason text`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`
- `unique (revision_id, ts_code, side)`

`planned_trade_date` 只能由真实交易日历或已存在的下一交易日行情确定。无法确定时保留空值和 `PENDING`，不能用自然日猜测。

保存版本时先持久化目标权重与方向；`target_quantity` 和 `remaining_quantity` 在真实 `T+1 open` 可用后计算。进入可执行状态后，两者必须是非负有限值，完成订单的 `remaining_quantity` 必须为零。

### 6.5 `portfolio_trades`

- `trade_id uuid primary key`
- `order_id uuid not null references portfolio_rebalance_orders`
- `portfolio_id text not null references research_portfolios`
- `revision_id uuid not null references portfolio_target_revisions`
- `ts_code text not null references securities`
- `trade_date date not null`
- `side text not null`
- `quantity numeric not null check (quantity > 0)`
- `price numeric not null check (price > 0)`
- `gross_amount numeric not null`
- `fee_bps numeric not null`
- `fee_amount numeric not null`
- `created_at timestamptz not null default now()`
- `unique (order_id, trade_date)`

唯一约束与工作流幂等键共同防止调度任务重复运行时产生重复成交。

### 6.6 `portfolio_cash_flows`

- `cash_flow_id uuid primary key`
- `portfolio_id text not null references research_portfolios`
- `flow_date date not null`
- `flow_type text not null`
- `amount numeric not null`
- `reference_type text not null`
- `reference_id text not null`
- `note text`
- `created_at timestamptz not null default now()`
- `unique (portfolio_id, reference_type, reference_id)`

初始资金形成 `INITIAL_CAPITAL` 流水。买卖成交的现金变化由成交账本投影，不重复写成外部资金流。

### 6.7 `portfolio_daily_nav`

- `portfolio_id text not null references research_portfolios`
- `valuation_date date not null`
- `cash numeric not null`
- `market_value numeric`
- `total_value numeric`
- `nav numeric`
- `benchmark_nav numeric`
- `status text not null`
- `coverage numeric`
- `reason text`
- `calculation_version text not null`
- `updated_at timestamptz not null default now()`
- `primary key (portfolio_id, valuation_date)`

净值记录是物化投影，可由现金流、成交和行情全量删除后重算；它不是成交事实源。

## 7. 调仓与成交规则

### 7.1 保存目标权重

保存操作在一个事务内完成：

1. 校验组合为 `ACTIVE`。
2. 校验证券属于当前支持的 A 股研究证券池。
3. 校验权重有限、非负、单项不超过 100%、总和不超过 100%。
4. 读取数据库最新真实交易日期作为 `signal_date`；没有行情时拒绝保存并展示数据不足。
5. 创建新的目标版本和目标项。
6. 将同组合尚未成交的旧计划显式标记为 `SUPERSEDED`。
7. 根据当前账本持仓、现金和目标版本创建新的调仓计划。

保存成功只表示目标版本和计划已持久化，不表示已经成交。

### 7.2 目标数量

调仓基准资产为信号日可验证的组合总资产。每只证券的目标金额为：

`目标金额 = 信号日组合总资产 × 目标权重`

目标买入数量根据计划成交日真实 `open` 计算，并按 100 股向下取整。由于 `T+1 open` 在保存时可能尚不存在，保存阶段不伪造最终成交数量；计划先保存目标权重与方向，执行阶段再计算可成交数量和现金约束。

卖出先于买入处理，使卖出释放的现金可以用于同一调仓日买入。任何买入均不得使现金为负。

### 7.3 T/T+1 与可交易性

- `T` 日收盘后形成信号。
- 只查找 `T` 之后的下一真实交易日。
- 成交价只使用该日 `PriceBar.adjusted_open` 对应的明确复权研究口径；原始 `open` 缺失时视为不可成交。
- `suspended=True` 时不可成交。
- 买入遇到 `limit_up=True` 不成交；卖出遇到 `limit_down=True` 不成交。
- 不使用 `close`、前收盘或其他证券价格替代。
- 无法成交的订单保留原因。一次计划中部分证券成交时，版本状态为 `PARTIAL`。

页面的数量与成本明确标注为“复权研究模拟口径”，避免与券商交割股数混淆。该口径复用当前回测使用的复权价格，保证公司行为期间收益连续；不声称还原实际分红到账和送转股交割明细。

### 7.4 成本、现金与收益

买入现金变化：

`现金减少 = 成交数量 × 成交价 + 成交费用`

卖出现金变化：

`现金增加 = 成交数量 × 成交价 - 成交费用`

成交费用：

`成交费用 = 成交金额 × transaction_cost_bps / 10000`

移动加权平均成本：

`新平均成本 = (原持仓成本金额 + 本次买入金额 + 本次买入费用) / 新持仓数量`

卖出不改变剩余股数的单位成本。已实现收益为卖出净收入减去卖出数量对应的账面成本；未实现收益为最新有效收盘市值减去剩余持仓账面成本。

## 8. 每日估值与指标

### 8.1 每日估值

从首次实际成交日开始，按每个真实交易日收盘估值：

`总资产 = 现金 + Σ(持仓数量 × 当日 adjusted_close)`

若任一有持仓证券缺少当日必要收盘行情，不使用旧价格填充；该日标记 `PARTIAL`，`market_value`、`total_value` 和 `nav` 不作为完整值展示。覆盖率按有真实当日价格的持仓市值权重计算，并保留缺失证券列表或原因。

基准净值只使用沪深300同日真实收盘价。缺少基准时组合净值仍可记录，但相对指标不可计算。

### 8.2 组合指标

从完整净值样本计算：

- 组合累计收益。
- 近一年收益；不足一年显示 `--`。
- 年化收益；样本不足时显示 `--`。
- 年化波动率。
- 最大回撤。
- Sharpe；无足够收益序列或波动率为零时显示 `--`。
- 相对沪深300收益。
- 月度胜率；没有足够完整月度样本时显示 `--`。
- 年化换手率。

指标函数必须返回数值或明确不可用原因，不能以 `0` 表示缺失。

### 8.3 持仓级指标

- 当前权重：最新完整估值日证券市值除以组合总资产。
- 首次买入日：该证券第一笔买入成交日。
- 当前复权研究价格：最新完整估值日 `adjusted_close`。
- 未实现收益、已实现收益和累计收益。
- 收益贡献：持仓及已实现收益对组合总收益的贡献。
- 研究分：估值日前最近可用 PIT 快照按当前选择模板运行时计算的综合分。
- 证据覆盖率：模板实际可用权重之和；低于 70% 时不展示综合分并标记证据不足。

## 9. 行业与因子暴露

### 9.1 行业暴露

对每只持仓使用 `effective_date <= valuation_date` 的最新行业记录。行业权重为持仓市值权重之和。没有有效行业记录的证券归入“未分类”，不能使用未来行业分类。

### 9.2 因子暴露

读取 `as_of_date <= valuation_date` 的最近一个 `COMPLETED` PIT 因子快照，且记录快照日期、`factor_version`、`pit_version` 和 `universe_version`。

六类因子为价值、质量、成长、动量、低波动和流动性。若现有内部键仍使用 `valuation`、`quality`、`growth`、`momentum`、`risk` 等名称，实施时必须从真实因子定义确认映射；没有真实低波动或流动性维度时，该维度显示不可用，不得把行业因子或其他字段改名冒充。

每个可用维度在同一快照全体可用证券上计算横截面 Z-Score：

`z = (证券因子分 - 截面均值) / 截面标准差`

截面标准差为零或样本不足时，该维度不可用。组合暴露为各持仓 Z-Score 按当前市值权重加权的平均值。缺失项不补零，页面同时展示该维度实际覆盖权重。

## 10. 历史回放

用户选择组合目标版本、起止日期、沪深300基准、月度调仓频率和单边费率后创建独立 `portfolio_backtest` 研究运行。回放冻结该目标版本，不读取之后保存的新权重。

每个调仓期遵循：

`T 日收盘确认目标 -> T+1 下一交易日开盘成交 -> 每日收盘估值`

历史回放不得读取组合创建后的成交作为历史事实，也不得将回放收益展示为组合自创建以来的模拟实绩。运行参数保存目标版本编号、数据区间、基准、频率、成本和计算版本。

状态规则：

- 完整调仓期少于 2 个：`INSUFFICIENT_DATA`。
- 部分调仓期因必要行情缺失被跳过：`PARTIAL`。
- 全部请求期有效：`COMPLETED`。
- 不可交易原因和跳过区间写入输出审计信息。

## 11. 页面设计

组合页面迁入 `quant/portfolio_ui.py`，`streamlit_app.py` 只负责路由。优先使用 Streamlit 原生容器、指标、表格、表单和图表，不使用静态 HTML 伪造交互。

### 11.1 顶部组合区

- 组合切换。
- 新建组合：名称、初始资金、费率。
- 复制组合：复制配置与最新目标草案，不复制成交、现金或历史净值。
- 删除组合：需要确认；默认采用归档以保留审计记录。
- 展示数据截止日、PIT 日期、最近一次调仓状态和保存状态。

### 11.2 指标卡

- 组合数量。
- 当前持仓数。
- 已分配目标权重。
- 现金比例。
- 组合累计收益。
- 最大回撤。
- 研究证据覆盖率。

每张卡片展示口径或样本区间。不可用值显示 `--`。

### 11.3 持仓与权重

表格字段：股票、行业、目标权重、当前权重、首次买入日、模拟数量、移动平均成本、当前价格、未实现收益、已实现收益、累计收益、收益贡献、研究分、证据覆盖率和状态。

编辑目标权重后点击“保存权重”，自动创建新目标版本与调仓计划。提供等权分配、按研究分分配和归一化操作，但这些操作只修改草案；最终仍由保存动作形成可审计版本。“按研究分分配”只对覆盖率不低于 70% 的证券使用真实模板分，缺失证券保持原权重或由用户决定，不自动填零。

### 11.4 调仓记录

展示目标版本、信号日、计划成交日、真实成交日、证券、方向、权重变化、数量、成交价、费用、状态和原因。默认显示最近记录，可展开全部。

### 11.5 暴露与历史模拟

- 行业暴露按当前市值权重展示。
- 因子暴露显示六维 Z-Score、覆盖率、快照日期和版本。
- 组合自创建以来的模拟实绩展示净值与沪深300曲线。
- 历史回放区提供版本、区间、基准、月度频率和成本控件，以及累计收益、年化收益、最大回撤、Sharpe、超额收益、月度胜率和换手率。

### 11.6 诊断区

- 行情完整度和缺失证券。
- 个股与行业集中度。
- 最近因子快照日期、版本和覆盖率。
- PIT 检查结果。
- 待成交或部分成交计划。
- 最新行情和估值更新时间。

## 12. 服务与文件边界

- `quant/types.py`：组合领域类型和状态枚举。
- `quant/portfolio.py`：纯领域计算；不依赖 Streamlit，不直接发起外部请求。
- `quant/storage.py`：表结构、迁移兼容、事务型组合仓储和针对页面的有界查询。
- `quant/workflows.py`：创建组合、保存目标版本、处理待成交、重建估值和运行历史回放。
- `quant/portfolio_ui.py`：组合页面和输入校验展示。
- `streamlit_app.py`：页面路由和共享依赖注入。
- `quant/research_ui.py`：候选池与个股详情加入当前组合草案的导航衔接。

页面读取不得全量反序列化全部历史行情。组合查询应按组合证券和所需日期范围加载；昂贵的只读汇总使用有界缓存，写入成功后明确失效相关缓存。

## 13. 调度与幂等

保存权重后立即尝试处理计划；没有 `T+1` 行情时正常保持 `PENDING`。每次成功数据同步后调用待成交处理工作流，并重建受影响组合的估值。

幂等要求：

- 同一目标版本、证券和方向只有一个计划。
- 同一订单和交易日只有一笔成交。
- 调度任务重试只读取既有成交并继续未完成数量。
- 每日净值按组合和日期 upsert，输入相同则输出相同。
- 新版本显式取代未成交旧版本，已成交旧版本保留事实记录。

## 14. 错误与状态展示

- 没有行情：拒绝形成信号版本，显示数据不足。
- 没有下一交易日数据：计划 `PENDING`。
- 缺少 `open`、停牌、涨停买入或跌停卖出：订单保留明确原因。
- 部分证券成交：计划 `PARTIAL`，现金与已成交持仓照实记录。
- 缺少持仓日收盘价：当日估值 `PARTIAL`，不沿用旧价。
- 缺少基准：绝对净值可用，相对指标不可用。
- 缺少匹配 PIT 快照：因子暴露与研究分显示 `--`，持仓与净值仍可展示。
- 历史样本不足：年化收益、最大回撤、Sharpe 等无法计算的指标显示 `--`，不显示 `0.00%`。
- 后端请求失败后不能继续展示旧成功提示；页面展示本次真实状态。

公开页面不得暴露数据库异常堆栈、内部连接信息或敏感运行编号。

## 15. 测试策略

使用 `unittest`，由 pytest 收集。生产代码测试使用确定性 fixture 或 `InMemoryStore`，不得访问真实 Tushare。

### 15.1 领域测试

- 多组合资金、目标、持仓和成交相互隔离。
- 初始资金只形成一次。
- 权重负数、非有限值、单项或合计超过 100% 被拒绝。
- 未分配权重保持现金。
- 100 股取整和现金不足约束。
- 卖出先于买入。
- 移动平均成本、费用、已实现与未实现收益。
- 收益贡献、净值、年化收益、波动率、最大回撤、Sharpe、胜率和换手率。

### 15.2 时间与交易测试

- 保存日为真实 `T`，只在下一交易日 `T+1` 开盘成交。
- 没有未来行情时保持 `PENDING`，不回填。
- `open` 缺失不使用 `close`。
- 停牌、涨停买入和跌停卖出保持不可成交原因。
- 部分成交返回 `PARTIAL`。
- 重复执行不会重复成交或重复扣费。
- 新版本只取代尚未成交的旧计划。

### 15.3 PIT 与暴露测试

- 行业只使用 `effective_date <= valuation_date` 的记录。
- 因子只使用 `as_of_date <= valuation_date` 的完成快照。
- 横截面 Z-Score 和市值加权暴露正确。
- 缺失因子不补零并降低覆盖率。
- 模板覆盖率低于 70% 时研究分不可用。
- FACTOR 组合功能不读取 MODEL prediction，原有 MODEL 流程保持通过。

### 15.4 存储与 UI 测试

- Schema 包含全部账本表、约束和幂等唯一键。
- 保存目标版本与创建计划是原子操作。
- 复制组合不复制历史成交和净值。
- 归档组合保留审计数据。
- 页面展示真实 `PENDING`、`PARTIAL`、`INSUFFICIENT_DATA`。
- 不可用指标显示 `--`。
- 权重参数真实进入工作流，成功提示来自真实后端结果。
- 多组合切换、创建、复制、归档和保存权重形成完整用户路径。

## 16. 完成标准

实现只有在以下条件全部可验证时才算完成：

1. 组合页面实际调用新的组合工作流和持久化账本。
2. 保存权重真实创建目标版本和待成交计划。
3. 新行情同步后，符合条件的计划只成交一次。
4. 持仓、现金、成本、收益、行业和因子暴露可从真实输入重算。
5. 历史回放遵守 PIT、T/T+1、真实开盘和交易成本规则。
6. 缺失数据没有假值、静默替代或旧成功结果。
7. 相关单元、存储、工作流和 Streamlit 页面测试实际运行并通过。
8. 原有 FACTOR、MODEL、候选池、个股详情和历史验证测试未被破坏。
