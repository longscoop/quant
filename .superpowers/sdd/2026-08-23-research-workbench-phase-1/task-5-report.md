# Phase 1 Task 5 报告

## 变更

- 更新 `README.md`，新增“研究流程”章节：同步数据、构建覆盖预测日期的因子历史、选择因子运行中存在的预测日期、检查模型状态，并仅在模型状态为“已完成”时运行回测。
- 明确模型、因子和回测输出是研究辅助信息与历史结果，不构成个性化投资建议或收益保证。

## 验证

命令：

```text
docker compose build streamlit
docker compose run --rm --no-deps streamlit python -m unittest discover -s tests -v
```

结果：通过，`Ran 34 tests in 6.200s`，`OK`。

命令：

```text
docker compose up -d --force-recreate streamlit
docker compose logs --tail=20 streamlit
```

结果：Streamlit 容器启动成功，日志报告 `http://localhost:8501`；`docker compose ps` 显示 `quant-streamlit-1` 为 `Up`。

手动检查：打开总览并连接 Compose PostgreSQL 后，页面显示中文研究工作流、数据质量摘要及研究信号免责声明；对 `streamlit_app.py` 和 `README.md` 搜索“买入/卖出”无匹配。

注意：总览继续渲染时遇到既有 UI 问题：`streamlit_app.py:63` 将 `datetime.date` 直接传给 `st.metric`，导致 `TypeError: '2026-08-21' ... is not an accepted type`。因此无法在本次 Task 5 中完成所有主页面的交互式浏览；该问题不影响本任务 README 变更或 34 项自动化测试结果。
