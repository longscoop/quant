# 市场可扩展的量化研究工作台（v1：A 股）

面向个人与研究团队的股票研究工作台。v1 实际运行范围是中国 A 股，系统以 PostgreSQL 保存真实 Tushare 数据，并以 Qlib 作为 MODEL 核心研究框架。市场无关核心通过 `ResearchContext`、日历、代码映射、股票池、PIT 特征、可交易性、成本和整手 Provider 扩展；CN 默认配置只位于应用边界。港股 `.HK` canonical ID 已可进入领域模型，但 HK 日历、数据、训练和回测 Provider 尚未实现。

## 功能

- 仅处理沪深300成分股；数据同步按证券代码分批请求，兼容非 VIP Tushare 账户。
- 六类因子：价值、质量、成长、动量、低波动、流动性；仅使用截至计算日已知的数据。
- FACTOR 与 MODEL 是独立路径。MODEL 使用 `DataHandlerLP → DatasetH(train/valid/test) → qlib.contrib.model.gbdt.LGBModel → SignalRecord / IC / RankIC`；旧直接 LightGBM 仅保留在 legacy 命名空间用于显式对照。
- 每月调仓的 Top-N 多头回测；公开“历史回测实验”默认按月复用或补算 PIT 因子快照，以模板评分形成 FACTOR 信号，并记录交易成本、可交易性过滤、持仓和调仓明细。
- 多组合研究模拟账本：保存目标权重形成不可覆盖版本，严格按 `T` 日收盘信号、`T+1` 下一交易日真实开盘价模拟成交，并由成交、现金和每日收盘行情计算持仓成本、收益、回撤、行业/PIT 因子暴露及调仓记录。默认初始资金 100 万元、单边成本 5 bps，均可按组合配置。
- React + FastAPI 研究页面：工作台、选股、我的组合、历史回测实验四个主入口；个股详情由股票搜索或候选进入，行业观察与数据状态保留独立入口；普通用户只看研究证据与历史模拟，不接触运行编号、任务控制或底层错误。另提供受部署开关控制的管理员工作台，用于准备数据与诊断任务。

## 受控管理员部署前置条件

- Python 3.11+、PostgreSQL 16+，以及有效的 Tushare Token。
- 财务指标接口按单股请求；Token 的积分、频率和 VIP 权限决定可用数据范围。Token 不会写入数据库。

## 管理员工作台

管理员工作台默认关闭。仅在受控部署中设置 `QUANT_ADMIN_MODE=1`，然后重启 API。普通用户部署不要设置该变量。

`QUANT_ADMIN_MODE` 只是部署开关，不是登录或权限系统。如需同时提供公开页面与管理页面，请使用两个部署实例，并只在内部实例启用管理员模式。

管理员工作台按“数据同步 → 完整性检查 → 因子构建 → 模型生成”执行。Tushare Token 只从密码输入或 `TUSHARE_TOKEN` 环境变量读取，不写入数据库。

公开实例面向普通用户：API 服务端配置数据库连接，但不配置 Tushare Token 或管理员开关；前端容器不接收数据库连接、Token 或管理员任务配置。管理员功能只在独立受控部署中开启。

## Docker Compose

```bash
cp .env.example .env
# 仅内部管理员实例：在 .env 填写 TUSHARE_TOKEN、QUANT_ADMIN_MODE=1，并按需修改 POSTGRES_PASSWORD
docker compose up --build
```

首次部署或 `pyproject.toml` / `frontend/package-lock.json` 变更后，可先运行
`docker compose build api frontend` 预热基础镜像和依赖缓存；随后 `docker compose up`
会复用同一个 Python 镜像给 API、scheduler 和任务命令。`data/postgres` 始终作为
PostgreSQL 的宿主机卷挂载，不参与应用镜像构建。Qlib Recorder artifact 持久化在 `data/qlib`（容器内 `/app/data/qlib`）。

上述 Compose 配置用于受控的内部管理员部署；数据库连接由服务环境提供，而非在页面输入。打开 `http://localhost:5173` 使用研究工作台，浏览器 API 请求由前端容器转发到内部 `api:8000`，不需要打开 8000 端口。设置 `QUANT_ADMIN_MODE=1` 后重启 API，页面才会出现“管理员”入口。公开部署应作为独立实例运行，不设置管理员开关或 Token。旧 Streamlit 界面仅在执行 `docker compose --profile legacy up streamlit` 时启动。

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
.venv/bin/uvicorn backend.app.main:app --port 8000
npm --prefix frontend install
npm --prefix frontend run dev
```

## 数据补齐与每日同步

首次运行使用历史补数；它从 2020-01-01 起保留基础资料、日频行情、每日估值和财务报表原始版本：

```bash
python -m quant.cli init-db
python -m quant.cli sync-index-members --start-date 2020-01-01 --end-date 2026-08-31
python -m quant.cli backfill-data --start-date 2020-01-01
```

`backfill-data` 使用 `TUSHARE_TOKEN`，依次运行沪深300历史补数与沪深300基准开收盘价补数，输出两个运行 ID、运行状态和落库完整性报告。可用 `--end-date YYYY-MM-DD` 限定终点，或在受控部署中运行 `docker compose --profile jobs run --rm quant backfill-data --start-date 2020-01-01`。若任一任务失败、部分成功，或完整性报告仍有缺口，命令以非零状态退出；根据输出中的缺口及运行记录检查 Tushare 权限和数据覆盖后重试。报告检查最新行情日时点成分的行情、财务记录和当日估值，不代表所选历史区间逐日完整。

历史回测还需要当时的成分关系，因此首次补数先按月同步真实沪深300成分快照。上例中的 `2026-08-31` 是当时已完成的最后一个月末；之后运行时应改为最近已有快照的月末。该任务只写入每个快照日确实取得的完整 300 只成分；没有取得完整快照的月份会在运行记录中列出，命令以非零状态退出。补齐成分后，还需检查新增历史成员在回测区间的行情、估值和财务覆盖。月度接口在尚未产生新快照的当月可能不返回数据；此时任务会如实报告缺口。

针对已落库成分中缺少基础资料、行情、估值或财务记录的证券，可只补这些代码，避免重复请求已有成员：

```bash
python -m quant.cli backfill-member-gaps --start-date 2020-01-01 --end-date 2026-09-28
python -m quant.cli sync-suspensions --start-date 2020-01-01 --end-date 2026-09-29
python -m quant.cli backfill-valuation-gaps --start-date 2020-01-01 --end-date 2026-09-29
```

运行记录会列出仍无核心数据的代码及源接口错误。这个检查以证券是否至少有一条记录为界，不能替代回测区间的逐日覆盖审计。

`sync-suspensions` 按全部历史成分拉取 Tushare `suspend_d` 的每日停牌（S）与复牌（R）事件，保存原始记录并形成有实际复牌日期的停牌区间；任务按代码记录断点，可在源接口中断后用相同日期重试。最新日审计会单独列出有源记录证明停牌的成分，不把其无行情和无估值当作同步缺口。

`backfill-valuation-gaps` 逐日比对全部历史成分的真实行情与估值，只向 Tushare 请求有行情却缺估值的证券，并在写入后再次核对剩余日期；不会用默认估值代替源数据。

数据检查完成后可运行独立 FACTOR 历史验证（收盘信号、下一交易日开盘成交）：

```bash
python -m quant.cli factor-backtest --start-date 2024-09-01 --end-date 2026-08-31 --top-n 30 --cost-bps 10
```

命令输出实际运行 ID、状态与有效调仓期数；只有 `completed` 才输出绩效指标。`partial`、`insufficient_data` 或失败时以非零状态退出，并保留原因。FACTOR 历史验证连续持仓、逐日收盘估值：证券停牌且有真实停牌记录时，按最后可见收盘价暂时结转并记审计；受阻订单持续跟踪，恢复可交易后按实际开盘价成交并计费。缺少行情且没有停牌证据时仍标记数据不足。CLI 中原有的 `backtest --model-run-id` 用于 MODEL 路径。

使用 Docker 运行新命令前，先执行 `docker compose build streamlit` 更新共用的 `a-share-quant:dev` 镜像；代码修改不会自动进入已有镜像。完整性报告按最新行情日的真实沪深300成分快照计算，历史曾入选的证券不计入最新日缺口，并比较已落库的基准最新交易日；若基准自身也未更新，报告不能单独判断是否已追到今天。

`scheduler` 服务会在 Asia/Shanghai 的交易日 18:30 自动执行增量同步；启动 Compose 后保持该服务运行即可。增量行情回补最近 5 个交易日窗口，财务数据回补最近 400 天，以吸收数据修订。可用 `python -m quant.cli run-scheduler --once` 手动检查当前是否到期。

后续研究命令会输出包含 `run_id` 的 JSON；使用该 ID 串联阶段：

```bash
python -m quant.cli build-factors --start-date 2024-01-01 --end-date 2025-01-01
python -m quant.cli train --factor-run-id <因子运行ID> \
  --train-start 2020-01-02 --train-end 2022-12-30 \
  --valid-start 2023-02-01 --valid-end 2023-12-29 \
  --test-start 2024-02-01 --test-end 2025-01-01
python -m quant.cli infer --model-run-id <模型运行ID> --factor-run-id <最新因子运行ID> \
  --date 2025-02-28
python -m quant.cli backtest --model-run-id <模型运行ID> --top-n 30 --cost-bps 10
```

## 研究流程

MODEL 研究路径按以下顺序执行：

1. 同步数据（`sync-hs300`）。
2. 构建覆盖预测日期的因子历史（`build-factors`）；因子历史必须包含预测日期及其之前所需的数据。
3. 显式选择按交易日顺序的 train/valid/test；20 日 Label 在 train/valid 边界执行 purge，test 不参与 early stopping，再训练 Qlib LGBModel（`train`）。
4. 检查模型运行状态；最新未成熟 Label 的 PIT 特征通过 `infer` 从 Recorder 加载已训练模型独立预测，不进入历史 train/valid/test。
5. 仅在模型状态为“已完成”时运行回测（`backtest`）；如果模型不可训练或失败，请先按页面提示补齐数据或重新构建因子历史。

公开页面的“历史回测实验”是独立 FACTOR 路径，不读取 model run 或 prediction。页面根据所选区间生成月度调仓日，逐期检查并复用/补算版本化 PIT 因子快照，再按模板覆盖率规则实时评分；不足 70% 的证券不参与排名。运行过程显示真实的快照检查、补算、跳过和回测事件，最终以 `completed`、`partial` 或 `insufficient_data` 记录有效期数与跳过期数。MODEL 路径继续保留为独立研究方式。每个 DatasetH、模型、Recorder 和回测仅属于一个 market；未来 HK 会使用独立的 DatasetH、模型、Recorder 和回测，不默认与 CN 混合训练。

页面中的模型、因子和回测输出均为研究辅助信息与历史结果，不构成个性化投资建议，也不代表未来收益。

“我的组合”中的模拟实绩只来自组合创建后实际形成的目标版本和模拟成交；目标版本历史回放是独立研究结果，两者不会混为实际账户收益。行情尚未到达 `T+1` 时显示待成交，缺少开盘价、停牌或涨跌停时保留明确状态，不使用收盘价替代。系统不连接券商，也不产生实盘订单。

## 数据与风险

页面不会在刷新时请求 Tushare，只读取 PostgreSQL 中已同步的数据。每一次同步、因子构建、模型训练和回测均记录运行 ID、参数、状态、输出或错误。研究结果不构成投资建议；历史回测不代表未来收益。
# 前后端开发

前端与 API 使用独立进程；前端只通过 `/api/v1` 读取后端，不能获得数据库连接配置。

```bash
.venv/bin/uvicorn backend.app.main:app --reload --port 8000
npm --prefix frontend install
npm --prefix frontend run dev
docker compose up --build postgres api frontend
```

### 研究页面交互

- 工作台保留每日选股推荐，并明确显示真实研究日期；旧快照标记为最新可用推荐、今日待更新。
- 股票搜索按名称或代码查询，接口最多返回 50 条、页面每次取 20 条，不再加载全量股票下拉框。选股支持勾选后批量添加，每次明确选择目标组合；添加失败会说明已完成数量并允许继续添加剩余股票。
- 组合按独立详情页管理。待调整清单不代表持仓；目标比例输入为 0–100%，先预览建仓、加仓、减仓、清仓或保持，再保存模拟计划。资金参数校验失败保留输入，成功写入后的读取失败会明确提示并清除旧结果。
- 历史回测实验列表只读取保存的摘要与绩效，查看详情时才加载成交和曲线；区分设定区间与实际收益曲线区间。同名实验可按创建时间区分，部分完成的实验不展示完整绩效。
- 收益曲线支持悬停、点击和日期滑块。选择日期后仅重放该日及之前的已保存成交，展示买入日期、持仓成本、模拟股数、持仓涨跌幅和当日成交；缺少所选日行情的字段显示 `--`。
- 模拟股数包含复权和分红再投资的折算，允许小数，不等同券商整手持仓。成交价只在保存的复权开盘价与当日真实开盘行情一致时换算展示。
- 策略卡片的权重直接来自后端模板定义。“稳健价值（原高股息）”保留 `high_dividend` 标识，明确说明其没有独立高股息率筛选门槛。
