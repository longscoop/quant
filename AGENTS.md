# Repository Guidelines

> 核心原则：研究结果的真实性高于功能“看起来可用”。不确定时先验证事实，不得猜测、伪造或静默降级。

## Project Overview

本项目是面向个人与研究团队的股票研究工作台。

系统使用 PostgreSQL 保存真实 Tushare 数据，提供：

- 沪深300、创业板、港股成分股数据研究
- PIT（Point-in-Time）历史数据处理
- 价值、质量、成长、动量、低波动、流动性六类因子
- FACTOR 因子评分与 LightGBM MODEL 两条研究路径
- 月度 Top-N 多头历史验证
- 交易成本、可交易性、持仓和调仓记录
- Streamlit 研究页面及管理员数据工作台
- 完整运行状态与审计信息

系统定位：

`真实数据 -> PIT -> 因子/模型 -> 策略验证 -> 研究展示`

当前不提供券商账户接入、实盘下单或自动交易。

未来可以扩展新的数据源、市场、因子、模型、策略、调仓频率和分析能力，但应沿现有分层扩展，避免创建重复流程。

## Project Structure

核心代码位于 `quant/`：

- `streamlit_app.py`：Streamlit 入口
- `*_ui.py`：页面展示
- `quant/types.py`：领域模型
- `tests/`：自动化测试
- `tests/fixtures/`：测试页面
- `data/postgres/`：运行数据，不属于源码

新增功能优先复用现有模块、类型和调用链。

## Quant Rules

### PIT

历史日期 `T` 只能使用当时已经可见的数据：

- 行情、估值：`date <= T`
- 财务：`announcement_date <= T`
- 股票池：使用 T 时点真实成员关系

禁止使用未来数据、最新数据回填历史或产生幸存者偏差。

### FACTOR / MODEL

FACTOR：

`PIT -> factor snapshot -> template score -> Top N`

MODEL：

`PIT -> features -> LightGBM -> prediction -> Top N`

两条路径必须独立。

FACTOR 历史验证不得隐式依赖 `model run` 或 `model prediction`。

模型样本不足必须返回不可训练状态，不得构造预测结果。

### Factor Snapshot

历史因子使用：

- `factor_snapshot`
- `factor_snapshot_item`

快照保存 PIT 日期、版本、股票基础因子分及可用状态，不保存“质量成长”等模板最终综合分。

模板评分运行时计算。

模板实际可用权重：

- `>= 70%`：参与排名，并重新归一化权重
- `< 70%`：排除

### Backtest

默认：

`T 日收盘生成信号 -> T+1 下一交易日开盘成交 -> 每日收盘估值`

交易成本在成交时计算。

策略与基准必须使用一致的时间口径。

缺少必要行情时不得用其他字段静默替代。

## Engineering Rules

### 1. 先查事实，禁止幻想

使用任何表、字段、API、状态、Enum 或配置前，必须先从代码、Schema、migration、类型定义或真实返回结构确认。

不得根据名称或常见实现猜测系统已经具备某项能力。

需求与现有代码不一致时，先确认真实调用链：

`UI -> Service -> Store/任务 -> Result -> UI`

再修改。

### 2. 禁止假数据

生产代码禁止：

- 写死收益率、排名、因子分、持仓或日期
- 写死业务进度
- 缺失数据使用 `0` 冒充真实值
- 构造不存在的 prediction / factor / backtest 结果
- 请求失败后继续展示旧成功结果

Mock 只允许用于测试。

### 3. 禁止静默 fallback

禁止例如：

- `open` 缺失自动使用 `close`
- 历史因子缺失使用最新因子
- 历史财报缺失使用当前最新财报
- 历史股票池缺失使用当前股票池
- MODEL 缺失时构造 prediction

需要 fallback 时必须有明确规则、状态和测试。

### 4. UI 必须展示真实状态

页面状态必须来自真实后端结果。

`PARTIAL`、`INSUFFICIENT_DATA`、`NOT_TRAINABLE` 不得显示为“验证完成”。

没有真实百分比进度时，仅显示阶段型 loading，不得伪造 `65% -> 100%`。

样本不足时，无法计算的年化收益、最大回撤、夏普等显示 `--`，不得显示 `0.00%`。

## Coding Style

- Python 3.11+
- 四空格缩进
- `snake_case` 函数和模块
- `PascalCase` 类
- 核心领域逻辑使用明确类型注解
- 优先复用 `quant/types.py`
- 小范围修改，避免无关重构和格式化
- 禁止无解释的 magic value、临时字段和重复模型

## Testing & Verification

测试使用 `unittest`，由 pytest 收集。

使用确定性 fixture 或 `InMemoryStore`；外部服务必须 mock，测试禁止直接请求真实 Tushare。

量化核心修改根据范围覆盖：

- PIT 时间边界
- 快照命中与自动补算
- 模板覆盖率与权重归一化
- 历史股票池
- T/T+1 成交边界
- PARTIAL / INSUFFICIENT_DATA / NOT_TRAINABLE
- FACTOR 不依赖 MODEL
- 原有 MODEL 流程不被破坏

完成前必须确认：

1. 页面实际走目标流程
2. 参数真实进入业务层
3. 返回字段真实存在
4. 没有假数据、假进度或静默 fallback
5. PIT 和回测时间边界正确
6. 相关测试实际运行并通过

无法确认的内容必须写明“未验证”，不得推测已完成。

## Commands

- `python -m venv .venv && . .venv/bin/activate && pip install -e .`
- `pytest`
- `streamlit run streamlit_app.py`
- `docker compose up --build`
- `docker compose --profile jobs run --rm quant init-db`
- `python -m quant.cli --help`

## Security

禁止提交 `.env`、Token、密码和数据库 URL。

使用环境变量：

- `TUSHARE_TOKEN`
- `DATABASE_URL`
- `QUANT_ADMIN_MODE`

公开实例不得暴露管理员任务控制、内部运行编号、底层异常堆栈或敏感诊断信息。
