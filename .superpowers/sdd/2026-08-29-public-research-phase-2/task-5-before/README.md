# 沪深300量化研究工作台

面向个人与研究团队的 A 股研究工作台。系统以 PostgreSQL 保存真实 Tushare 数据，提供 PIT（Point-in-Time）因子、因子评分/LightGBM 预测、月度 Top-N 多头回测及完整运行审计。它不提供券商账户接入、下单或自动交易。

## 功能

- 仅处理沪深300成分股；数据同步按证券代码分批请求，兼容非 VIP Tushare 账户。
- 六类因子：价值、质量、成长、动量、低波动、流动性；仅使用截至计算日已知的数据。
- 因子评分和 LightGBM 两条模型路径；模型样本不足会明确标记为不可训练。
- 每月调仓的 Top-N 多头回测，包含交易成本、可交易性过滤、持仓和调仓记录。
- Streamlit 公开研究页面：今日机会、股票池、个股研究、行业景气、策略实验室、组合、回测和数据状态；另提供受部署开关控制的管理员工作台，用于准备数据与诊断任务。

## 受控管理员部署前置条件

- Python 3.11+、PostgreSQL 16+，以及有效的 Tushare Token。
- 财务指标接口按单股请求；Token 的积分、频率和 VIP 权限决定可用数据范围。Token 不会写入数据库。

## 管理员工作台

管理员工作台默认关闭。仅在受控部署中设置 `QUANT_ADMIN_MODE=1`，然后重启 Streamlit。普通用户部署不要设置该变量。

`QUANT_ADMIN_MODE` 只是部署开关，不是登录或权限系统。如需同时提供公开页面与管理页面，请使用两个部署实例，并只在内部实例启用管理员模式。

管理员工作台按“数据同步 → 完整性检查 → 因子构建 → 模型生成”执行。Tushare Token 只从密码输入或 `TUSHARE_TOKEN` 环境变量读取，不写入数据库。

公开实例面向普通用户：不配置 `DATABASE_URL` 或 Tushare Token，也不显示管理员入口、数据库配置或 Token 指引。数据库连接和 Token 仅由受控内部管理员部署的运维人员配置；不要把这些值保存、打印或传给普通用户。

## Docker Compose

```bash
cp .env.example .env
# 仅内部管理员实例：在 .env 填写 TUSHARE_TOKEN、QUANT_ADMIN_MODE=1，并按需修改 POSTGRES_PASSWORD
docker compose up --build
```

上述 Compose 配置用于受控的内部管理员部署；数据库连接由服务环境提供，而非在侧栏输入。设置 `QUANT_ADMIN_MODE=1` 后打开 `http://localhost:8501`，在“管理员”页面完成首次数据同步。公开部署应作为独立实例运行，且不要设置管理员开关、数据库连接或 Token。

CLI 同步/研究任务可由 cron 调用：

```bash
docker compose --profile jobs run --rm quant init-db
docker compose --profile jobs run --rm quant sync-hs300 --start-date 2024-01-01 --end-date 2025-01-01
docker compose --profile jobs run --rm quant audit-data --universe hs300
docker compose --profile jobs run --rm quant build-factors --start-date 2024-01-01 --end-date 2025-01-01
```

## 本机运行

以下命令仅用于受控的内部管理员部署，不适用于普通用户的公开页面：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
export DATABASE_URL='postgresql://quant:quant@localhost:5432/quant'
export TUSHARE_TOKEN='你的 Token'
export QUANT_ADMIN_MODE=1
python -m quant.cli init-db
python -m quant.cli sync-hs300 --start-date 2024-01-01 --end-date 2025-01-01
streamlit run streamlit_app.py
```

## 数据补齐与每日同步

首次运行使用历史补数；它从 2020-01-01 起保留基础资料、日频行情、每日估值和财务报表原始版本：

```bash
python -m quant.cli sync-universe --universe hs300 --mode backfill --start-date 2020-01-01
python -m quant.cli audit-data --universe hs300
```

`scheduler` 服务会在 Asia/Shanghai 的交易日 18:30 自动执行增量同步；启动 Compose 后保持该服务运行即可。增量行情回补最近 5 个交易日窗口，财务数据回补最近 400 天，以吸收数据修订。可用 `python -m quant.cli run-scheduler --once` 手动检查当前是否到期。

后续研究命令会输出包含 `run_id` 的 JSON；使用该 ID 串联阶段：

```bash
python -m quant.cli build-factors --start-date 2024-01-01 --end-date 2025-01-01
python -m quant.cli train --factor-run-id <因子运行ID> --prediction-date 2025-01-01
python -m quant.cli backtest --model-run-id <模型运行ID> --top-n 30 --cost-bps 10
```

## 研究流程

按以下顺序完成一次研究：

1. 同步数据（`sync-hs300`）。
2. 构建覆盖预测日期的因子历史（`build-factors`）；因子历史必须包含预测日期及其之前所需的数据。
3. 从该因子运行实际包含的日期中选择预测日期，再训练模型（`train`）。
4. 检查模型运行状态；只有状态为“已完成”时，才进入回测。
5. 仅在模型状态为“已完成”时运行回测（`backtest`）；如果模型不可训练或失败，请先按页面提示补齐数据或重新构建因子历史。

页面中的模型、因子和回测输出均为研究辅助信息与历史结果，不构成个性化投资建议，也不代表未来收益。

## 数据与风险

页面不会在刷新时请求 Tushare，只读取 PostgreSQL 中已同步的数据。每一次同步、因子构建、模型训练和回测均记录运行 ID、参数、状态、输出或错误。研究结果不构成投资建议；历史回测不代表未来收益。
