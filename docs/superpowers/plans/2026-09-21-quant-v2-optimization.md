# Quant v2 Optimization Plan

## Goal

把当前 A 股研究 MVP 收敛为可重复、可审计、可扩展的量化组合研究系统。优先保证研究正确性，其次统一执行与组合语义，最后再扩展产品前端与多市场能力。

## Execution rules

每个 Task 按以下顺序执行：

1. 测试先行：先补充能暴露问题的单元/集成测试。
2. 最小实现：只修改当前 Task 必需的生产代码。
3. 回归测试：运行相关测试和完整测试。
4. 自检：确认 PIT、市场语义、数据覆盖、异常状态没有被静默降级。
5. Commit：每个 Task 独立提交，提交信息说明行为变化。

不以 UI 掩盖研究数据缺陷；当数据不足时必须返回明确状态和原因。

## Phase 0 — Repository and security baseline

### Task 01 — Remove tracked secrets and generated artifacts
- 删除已跟踪的 `.env`，只保留 `.env.example`。
- 扩展 `.gitignore`：`.env`、`.DS_Store`、`*.egg-info/`、`frontend/node_modules/`、`frontend/dist/`、`mlruns/`、`tmp/`、运行时 artifact。
- 说明：历史提交中的 Token 仍必须在 Tushare 侧轮换，单纯删除文件不能撤销已泄露凭据。

### Task 02 — Add CI quality gate
- Python：pytest + compile/import smoke。
- Frontend：npm test + build。
- 禁止提交 `.env` 和常见生成目录。

## Phase 1 — Quant correctness

### Task 03 — Fix risk-factor direction semantics
- 最大回撤使用“损失幅度”或显式方向定义，保证较小回撤得到更高风险分。
- 对所有 lower-is-better 指标增加排序测试。

### Task 04 — Make factor taxonomy canonical
Canonical dimensions:
- valuation
- quality
- growth
- momentum
- low_volatility
- liquidity
- industry

统一 `factors_v1.py`、templates、portfolio exposure、API/UI 命名；提供旧字段兼容迁移层。

### Task 05 — Implement real industry factor
首版至少包含：
- 行业 20/60/120 日相对动量。
- 行业内盈利增长中位数/分位数。
- 行业 breadth（上涨证券比例或正动量比例）。
- 行业估值相对历史/横截面位置。

行业样本不足时显式 unavailable，不用空指标伪装成功。

### Task 06 — Separate liquidity from risk
- turnover_rate 不再作为 risk 指标。
- 建立 liquidity 独立分数。
- 风险维度只表达 volatility / drawdown / beta / financial-risk 等风险含义。

## Phase 2 — PIT data integrity

### Task 07 — Historical security status
建立时序证券状态：
- ST / *ST / delisting-warning 等。
- effective_from / effective_to。
- PIT 查询 `security_status(ts_code, as_of)`。

### Task 08 — Historical industry classification
- 禁止把当前行业赋给整个历史区间。
- 存储行业分类版本和生效区间。
- 因子和组合暴露按 as-of 查询。

### Task 09 — Tradability facts
把停牌、涨跌停、上市天数等整理成统一可交易性快照：
- tradable_buy
- tradable_sell
- suspended
- limit_up
- limit_down
- is_st
- listing_days

FACTOR / MODEL / PORTFOLIO / BACKTEST 共用同一 Provider。

### Task 10 — Financial availability timestamps
- 区分 report_period / ann_date / first_ann_date / retrieved_at / revision。
- 明确“何时可用于研究”的 available_at。
- 对财报修订增加 PIT 回放测试。

### Task 11 — Historical universe quality
- 校验沪深300历史成分快照连续性。
- 不允许使用当前成分替代历史成分。
- 输出每期 universe size / missing member evidence。

## Phase 3 — Factor snapshots and coverage

### Task 12 — Partial factor snapshot semantics
将 snapshot 状态从“任一证券缺数据则整期 partial”改为横截面覆盖判定：
- eligible_count
- scored_count
- tradable_count
- coverage
- exclusion reasons

建议默认：
- coverage >= 90%: VALID
- 80% <= coverage < 90%: DEGRADED
- coverage < 80%: INVALID

### Task 13 — Persist factor rows outside research_runs JSON
新增结构化 factor snapshot item / metric 存储或 Parquet artifact。
`research_runs` 只保存引用、参数、状态和汇总，不保存多年全量 feature payload。

### Task 14 — Add factor diagnostics
每个因子输出：
- coverage
- distribution
- cross-sectional correlation
- IC / RankIC
- turnover
- decile return
- stability by year / regime

## Phase 4 — Model validation

### Task 15 — Align labels with executable returns
把标签从 `T close -> T+20 close` 改为实际可交易口径，例如：
`T+1 open -> T+20 close` 的股票超额收益。

### Task 16 — Walk-forward evaluation
按交易日滚动 train/valid/test，保留 purge/embargo。
汇总 OOS：
- IC
- RankIC
- top-bottom spread
- hit rate
- turnover
- drawdown

### Task 17 — Model registry contract
每个模型运行保存：
- market/context
- feature snapshot/version
- label definition
- split definition
- parameters
- recorder/model artifact
- OOS metrics

## Phase 5 — Unified execution and backtest

### Task 18 — One execution engine
FACTOR、MODEL、手工组合统一输出 `TargetPortfolio`，后续统一：
`TargetPortfolio -> Orders -> Execution -> Trades -> Holdings -> NAV`。

### Task 19 — Real transaction cost
至少支持：
- buy commission
- sell commission
- stamp tax（按适用日期/市场）
- minimum commission
- slippage
- turnover-based cost

### Task 20 — Remove fixed turnover assumptions
禁止 `turnover=1.0`、`annualized_turnover=12.0` 这类占位值。
根据前后目标权重和真实成交计算。

### Task 21 — Qlib parity
保留双引擎对账，但共享：
- signals
- target weights
- tradability
- execution date
- order quantities
- cost contract

无法解释的差异必须使 comparison 非 completed。

## Phase 6 — Portfolio construction

### Task 22 — Portfolio optimizer
目标函数：
`alpha - risk_penalty - turnover_penalty - transaction_cost_penalty`

支持约束：
- sum(weights)=1
- max stock weight
- holding count
- industry active-weight limit
- turnover limit
- liquidity/tradability eligibility

### Task 23 — Risk model and attribution
至少提供：
- industry exposure
- factor exposure
- concentration
- volatility
- drawdown
- beta
- active return attribution

### Task 24 — Rebalance proposal
生成“当前组合 -> 建议目标组合”的差异：
- buy/sell/hold
- target weight
- expected turnover
- estimated cost
- binding constraints

只作为研究模拟，不生成券商订单。

## Phase 7 — Storage and performance

### Task 25 — Query-scoped repositories
逐步移除全量 `load_memory()`：
- prices(codes, range)
- financial_pit(codes, as_of)
- universe(as_of)
- factor_snapshot(date/version)
- predictions(run/date)

### Task 26 — Artifact boundary
- PostgreSQL：元数据、PIT 索引、组合账本、审计。
- Parquet/Qlib Provider：大规模 feature / label / prediction series。
- MLflow：模型和实验 artifacts。

## Phase 8 — Product/API

### Task 27 — Complete research API
补齐：
- stocks
- industries
- factors
- models
- backtests
- portfolios
- data-quality

### Task 28 — Consolidate UI roles
- Streamlit：内部研究/管理员工作台。
- React：用户研究产品。
- React 不直接依赖 quant 内部实现，只使用 API。

### Task 29 — Research reproducibility page
任何结果可显示：
- as_of
- universe snapshot
- factor/model version
- source snapshot
- execution assumptions
- run id
- exclusion/coverage summary

## Phase 9 — Multi-market readiness

### Task 30 — HK implementation
只有在 CN 主链稳定后实现：
- HK calendar
- HK provider
- lot size
- transaction cost
- tradability
- currency
- benchmark
- independent Qlib dataset/model/backtest

不默认 CN/HK 混合训练。

## Definition of Done for v2 foundation

A 股 HS300 从 2020 至今可以：
1. 每日增量同步并给出数据质量结论。
2. 任意历史 as-of 重建当时可见 universe、财务、行业和可交易性。
3. 生成可解释因子截面，允许局部缺失并记录覆盖率。
4. 训练 Qlib 模型并执行 walk-forward OOS 验证。
5. FACTOR 与 MODEL 都进入同一组合构建和执行引擎。
6. T 日信号只允许 T+1 及之后的可成交价格进入收益。
7. 回测成本、换手、停牌和涨跌停来自真实执行记录。
8. 模拟组合与历史回测共享相同交易语义。
9. 任意页面结果都能追溯到数据快照、因子/模型版本与运行记录。
