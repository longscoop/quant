# API and frontend foundation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first independently deployable React + FastAPI research path (home and candidate pool), while removing the duplicate portfolio controls from the existing Streamlit stock-detail page.

**Architecture:** FastAPI adapts existing `quant.research` projections through a testable, Streamlit-free page-model module; it never reimplements PIT, factors, model ranking, or database access. React consumes versioned JSON DTOs from the API and renders explicit data states. Streamlit remains a compatibility frontend during this phase.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest/unittest, React 18, TypeScript, Vite, Vitest, React Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-31-frontend-backend-separation-design.md`

## Global constraints

- Keep `quant` as the sole owner of PIT, factor, model, portfolio, and backtest domain logic.
- Historical data and T/T+1 semantics must not change; API adapters must not invent values or fallback to current data.
- Return explicit status/reason values; UI shows missing numeric values as `--` or “不可用”, never `0`.
- Do not expose `DATABASE_URL`, Tushare tokens, stack traces, raw database exceptions, or administrator-only diagnostics to public clients.
- Tests use `InMemoryStore` or deterministic fixtures and never request real Tushare data.
- Preserve all existing Streamlit behavior except removal of the stock-detail “我的研究组合” card and its controls.
- The workspace currently has no Git metadata; omit the commit command until it is restored.

## File structure

| Path | Responsibility |
| --- | --- |
| `quant/research_api.py` | Streamlit-free research home and candidate-pool page-model projections shared by API and legacy UI. |
| `backend/app/main.py` | FastAPI app factory, CORS from an explicit environment variable, router registration, and opaque exception handling. |
| `backend/app/dependencies.py` | `PostgresStore` dependency built only from `DATABASE_URL`. |
| `backend/app/schemas/research.py` | Pydantic response models for the phase-one read API. |
| `backend/app/routers/research.py` | `GET /api/v1/research/home` and `GET /api/v1/research/candidates` adapters. |
| `backend/tests/test_research_routes.py` | HTTP contracts using a dependency-overridden deterministic store. |
| `frontend/` | Vite React project; its package manifest, API client, routes, components, and tests are fully independent from Python UI code. |
| `quant/research_ui.py` | Legacy UI updated to consume page models where feasible and to remove only the duplicate detail portfolio controls. |
| `tests/test_public_research_ui.py` | Regression test that validates the legacy detail page lacks the removed controls. |
| `Dockerfile`, `docker-compose.yml` | Separate API and frontend build/run services without deleting the existing Streamlit service in this phase. |

---

### Task 1: Lock in removal of duplicate stock-detail portfolio controls

**Files:**
- Modify: `tests/test_public_research_ui.py`
- Modify: `quant/research_ui.py:228-287, 474-478`

**Interfaces:**
- Consumes: `render_research_stock_detail(st, store) -> None`.
- Produces: Stock-detail rendering without portfolio read/write widgets or portfolio navigation side effects.

- [ ] **Step 1: Write the failing regression test**

Add a test using the existing `public_research_stock_detail_app.py` fixture. It must render the healthy stock detail and assert that no `AppTest.button` labels equal `加入研究组合`, `已加入`, `检查我的组合`, or `保存目标权重`, and that `“我的研究组合”` and `“当前目标权重”` are absent from rendered text. Name it `test_stock_detail_does_not_render_duplicate_portfolio_controls` because reintroducing the duplicate side card must make it fail.

```python
def test_stock_detail_does_not_render_duplicate_portfolio_controls(self):
    at = self._stock_detail_app("healthy")
    self.assertEqual(len(at.exception), 0)
    labels = [button.label for button in at.button]
    self.assertFalse(set(labels) & {"加入研究组合", "已加入", "检查我的组合", "保存目标权重"})
    rendered = self._rendered(at)
    self.assertNotIn("我的研究组合", rendered)
    self.assertNotIn("当前目标权重", rendered)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_public_research_ui.py::PublicResearchHomeTests::test_stock_detail_does_not_render_duplicate_portfolio_controls -v`

Expected: FAIL because the current healthy fixture renders the “我的研究组合” card and its controls.

- [ ] **Step 3: Implement the smallest removal**

Delete `_portfolio_position`, `_save_portfolio_position`, and `_render_detail_portfolio_controls` from `quant/research_ui.py`. Replace the two-column financial/detail card block with a single bordered financial container:

```python
with st.container(border=True):
    _render_stock_financials(st, advanced)
```

Do not alter `_show_candidate_actions`, `_portfolio_codes`, or `_active_portfolio_id`; research home and candidate pool remain valid “加入研究组合” sources.

- [ ] **Step 4: Run focused UI tests to verify green**

Run: `pytest tests/test_public_research_ui.py -v`

Expected: PASS, including existing candidate-pool and stock-detail tests.

- [ ] **Step 5: Run regression tests around the remaining portfolio entry points**

Run: `pytest tests/test_portfolio_ui.py tests/test_public_research_ui.py -v`

Expected: PASS. This proves only the detail card was removed; the independent portfolio workbench remains covered.

### Task 2: Extract testable home and candidate page models

**Files:**
- Create: `quant/research_api.py`
- Create: `tests/test_research_api.py`
- Modify: `quant/research_ui.py:1-190, 553-640`

**Interfaces:**
- Consumes: a Store-compatible object with `load_page_memory`, `load_memory`, `data_quality`, `latest_trade_date`, `is_trade_day`, `latest_run_summary`, or `list_runs`.
- Produces: `research_home_page(store, *, today: date) -> dict` and `research_candidate_page_model(store, *, filters: ResearchCandidateFilters, page: int, page_size: int) -> dict`.
- `ResearchCandidateFilters` is a frozen dataclass with fields `tendency`, `confidence`, `risk`, `industry`, `query`, `min_score`, and `min_coverage`; all fields default to the existing UI's “all/no filter” meaning.

- [ ] **Step 1: Write failing pure-projection tests**

Use a deterministic `InMemoryStore` modeled on `PublicResearchFixtureStore` so tests do not depend on Streamlit. Assert literal page-model results for a complete fixture and an empty fixture.

```python
def test_research_home_page_exposes_real_freshness_and_prioritized_candidates(self):
    result = research_home_page(_FixtureStore("healthy"), today=date(2026, 8, 29))
    self.assertEqual(result["status"], "COMPLETED")
    self.assertEqual(result["freshness"]["latest_trade_date"], "2026-08-27")
    self.assertEqual(result["freshness"]["state"], "可信")
    self.assertEqual(result["candidates"][0]["code"], "000001.SZ")

def test_empty_candidate_page_is_not_reported_as_completed(self):
    result = research_candidate_page_model(_FixtureStore("empty"), filters=ResearchCandidateFilters(), page=1, page_size=10)
    self.assertEqual(result["status"], "INSUFFICIENT_DATA")
    self.assertEqual(result["reason"], "暂时没有可研究的候选。")
```

- [ ] **Step 2: Run the pure-projection tests to verify red**

Run: `pytest tests/test_research_api.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quant.research_api'`.

- [ ] **Step 3: Implement only the shared page models**

Create `quant/research_api.py`. Use `research_candidate_rows`, `latest_research_snapshot_date`, `research_data_freshness`, `filter_research_candidates`, and `research_candidate_page` from `quant.research`; reuse the safe store compatibility calls from `quant.research_ui` rather than importing Streamlit. Normalize each public candidate to this stable DTO-shaped dictionary:

```python
{
    "name": row["名称"], "code": row["代码"], "industry": row["行业"],
    "tendency": row["研究倾向"], "confidence": row["数据可信度"],
    "risk": row["风险水平"], "as_of_date": row["数据日期"],
    "core_advantage": row["核心优势"], "primary_risk": row["主要风险"],
    "score": row["专业详情"]["模型分数"] or row["专业详情"]["因子综合分"],
    "coverage": row["专业详情"]["数据覆盖率"],
}
```

`research_home_page` returns `COMPLETED` only when candidates exist; otherwise return `INSUFFICIENT_DATA` with the literal public reason in the test. `research_candidate_page_model` returns `COMPLETED` for a non-empty filtered result and `INSUFFICIENT_DATA` when no candidates or no filtered candidates exist. Do not change `quant.research` ordering or evidence semantics.

- [ ] **Step 4: Run pure-projection tests to verify green**

Run: `pytest tests/test_research_api.py tests/test_public_research.py -v`

Expected: PASS. The existing PIT candidate evidence tests prove the adapter did not replace research projection logic.

- [ ] **Step 5: Make Streamlit read the same model without changing its user-visible content**

Update `render_research_home` and `render_research_candidate_pool` to use the page-model output for candidates, freshness, filters, and pagination. Keep Streamlit-specific session state, button routing, and portfolio actions in `quant/research_ui.py`.

Run: `pytest tests/test_public_research_ui.py -v`

Expected: PASS with the same home/pool assertions as before.

### Task 3: Create FastAPI application and safe dependencies

**Files:**
- Modify: `pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/app/dependencies.py`
- Create: `backend/app/routers/__init__.py`
- Create: `backend/tests/test_app.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI` and `get_store() -> PostgresStore`.
- Consumes: `DATABASE_URL` and optional comma-separated `QUANT_CORS_ORIGINS`; no other source supplies a database connection string.

- [ ] **Step 1: Write failing HTTP app tests**

```python
def test_health_endpoint_reports_service_without_database_access(self):
    response = TestClient(create_app()).get("/api/v1/health")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json(), {"status": "ok"})

def test_unexpected_route_error_returns_opaque_message(self):
    app = create_app()
    @app.get("/test-error")
    def raise_database_error():
        raise RuntimeError("postgresql://secret@example.invalid/quant")
    response = TestClient(app, raise_server_exceptions=False).get("/test-error")
    self.assertEqual(response.status_code, 500)
    self.assertEqual(response.json()["detail"], "服务暂时不可用，请稍后重试。")
    self.assertNotIn("secret", response.text)
```

- [ ] **Step 2: Verify red**

Run: `pytest backend/tests/test_app.py -v`

Expected: FAIL because `backend.app.main` and FastAPI are not installed.

- [ ] **Step 3: Add minimal application infrastructure**

Add `fastapi>=0.115`, `uvicorn[standard]>=0.30`, and `httpx>=0.27` to the Python project dependencies. Implement `get_store` to raise a generic configuration error when `DATABASE_URL` is absent and otherwise return `PostgresStore(os.environ["DATABASE_URL"])`. Implement `create_app` with the health route, explicit CORS origins parsed from `QUANT_CORS_ORIGINS` (empty means no browser origins), and a catch-all exception handler returning only the literal opaque message. Do not catch `HTTPException` in that handler.

- [ ] **Step 4: Verify app tests green**

Run: `pytest backend/tests/test_app.py -v`

Expected: PASS.

### Task 4: Publish versioned research read endpoints

**Files:**
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/research.py`
- Create: `backend/app/routers/research.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_research_routes.py`

**Interfaces:**
- Consumes: `research_home_page`, `research_candidate_page_model`, and FastAPI dependency `get_store`.
- Produces: `GET /api/v1/research/home` and `GET /api/v1/research/candidates?tendency=&confidence=&risk=&industry=&query=&min_score=&min_coverage=&page=1&page_size=10`.

- [ ] **Step 1: Write failing route-contract tests**

Override `get_store` with the deterministic fixture store. Assert an exact public contract instead of database dictionaries:

```python
def test_home_route_returns_stable_public_research_contract(self):
    response = self.client.get("/api/v1/research/home")
    self.assertEqual(response.status_code, 200)
    body = response.json()
    self.assertEqual(body["status"], "COMPLETED")
    self.assertEqual(body["freshness"]["latest_trade_date"], "2026-08-27")
    self.assertEqual(body["candidates"][0]["code"], "000001.SZ")
    self.assertNotIn("run_id", response.text)

def test_candidate_route_rejects_zero_page_size(self):
    response = self.client.get("/api/v1/research/candidates?page_size=0")
    self.assertEqual(response.status_code, 422)
```

- [ ] **Step 2: Verify red**

Run: `pytest backend/tests/test_research_routes.py -v`

Expected: FAIL with 404 for both requested routes.

- [ ] **Step 3: Implement DTOs and route adapters**

Model candidate numeric fields as `float | None`, date strings as `str | None`, and state/reason as non-empty strings. Require `page >= 1`, `1 <= page_size <= 100`, `0 <= min_score <= 100` and `0 <= min_coverage <= 1`. Route functions call only `quant.research_api`; no SQL, no Store internals, and no fabricated default numbers. Return `200` for an explicit `INSUFFICIENT_DATA` page model because the request succeeded and the body communicates the truthful domain state.

- [ ] **Step 4: Verify route contracts green**

Run: `pytest backend/tests/test_research_routes.py backend/tests/test_app.py tests/test_research_api.py -v`

Expected: PASS.

### Task 5: Scaffold React frontend and render home and candidate pages

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/research.ts`
- Create: `frontend/src/features/research/ResearchHomePage.tsx`
- Create: `frontend/src/features/research/CandidatePoolPage.tsx`
- Create: `frontend/src/features/research/research.test.tsx`

**Interfaces:**
- Consumes: the two phase-one `/api/v1/research/*` DTOs.
- Produces: browser routes `/` and `/candidates`, with direct navigation and explicit loading, domain-empty, and transport-error states.

- [ ] **Step 1: Write failing React component tests**

Use MSW or a fetch mock at the HTTP boundary. Assert consumer-visible behavior, not mock calls:

```tsx
it("shows API-backed candidate evidence on the home page", async () => {
  server.use(http.get("/api/v1/research/home", () => HttpResponse.json(healthyHome)));
  render(<ResearchHomePage />);
  expect(await screen.findByText("研究样本1（000001.SZ）")).toBeInTheDocument();
  expect(screen.getByText("盈利质量相对较强。")).toBeInTheDocument();
});

it("does not turn insufficient data into a zero-valued candidate table", async () => {
  server.use(http.get("/api/v1/research/candidates", () => HttpResponse.json(insufficientCandidates)));
  render(<CandidatePoolPage />);
  expect(await screen.findByText("暂时没有可研究的候选。")).toBeInTheDocument();
  expect(screen.queryByText("0.00")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify red**

Run: `npm --prefix frontend test -- --run`

Expected: FAIL because the React project and page components do not exist.

- [ ] **Step 3: Implement the minimal frontend**

Use Vite React TypeScript with `react-router-dom`. `client.ts` reads `VITE_API_BASE_URL`, defaults only to the same-origin empty prefix, parses JSON, and raises a user-safe transport error without displaying raw bodies. Render all displayed facts directly from DTO fields. The candidate page exposes the API's existing filters and sends their values as query parameters; it renders API `total`, `current_page`, and `page_count`, and never calculates a replacement ranking, coverage, or status.

Do not add a portfolio control to either page. Do not create a stock-detail page in this phase.

- [ ] **Step 4: Verify frontend green**

Run: `npm --prefix frontend test -- --run && npm --prefix frontend run build`

Expected: both commands exit 0.

### Task 6: Define separate development and container entry points

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Modify: `README.md`

**Interfaces:**
- Produces: `api` service at port `8000`, `frontend` service at port `5173` for development/static serving, existing `streamlit` compatibility service at port `8501`.
- Consumes: `DATABASE_URL`, `QUANT_CORS_ORIGINS`, `VITE_API_BASE_URL`, and the existing PostgreSQL environment variables.

- [ ] **Step 1: Write a failing deployment-contract test**

Add `tests/test_delivery_contract.py::test_compose_config_keeps_database_credentials_out_of_frontend`. The test runs `docker compose config --format json` with `subprocess.run(..., check=True, capture_output=True, text=True)`, parses the emitted JSON, and asserts that `services` contains `api`, `frontend`, and `streamlit`; that the API command contains `uvicorn backend.app.main:app`; and that the rendered `frontend` environment has no `DATABASE_URL` key. Skip only when the Docker executable is not installed. This runs the compose artifact and verifies its effective configuration rather than grepping its source text.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_delivery_contract.py::DeliveryContractTests::test_compose_config_keeps_database_credentials_out_of_frontend -v`

Expected: FAIL because `api` and `frontend` do not exist.

- [ ] **Step 3: Implement container and run documentation changes**

Keep the Python image for `api`, set its command to `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`, and pass `DATABASE_URL` only to Python services. Use a multistage Node build for `frontend`, inject only `VITE_API_BASE_URL` at build/runtime as appropriate, and publish its static files through Nginx. Keep Streamlit unchanged as a compatibility service. Update README with exact development commands:

```bash
uvicorn backend.app.main:app --reload --port 8000
npm --prefix frontend install
npm --prefix frontend run dev
docker compose up --build postgres api frontend streamlit
```

- [ ] **Step 4: Verify deployment contract and phase regression suite**

Run: `pytest tests/test_delivery_contract.py tests/test_research_api.py backend/tests tests/test_public_research_ui.py tests/test_portfolio_ui.py -v && npm --prefix frontend test -- --run && npm --prefix frontend run build`

Expected: all commands exit 0. If Docker is available, additionally run `docker compose config` and inspect that no frontend environment contains `DATABASE_URL`.

## Plan self-review

Coverage: Task 1 implements the requested duplicate-control removal; Tasks 2–4 establish real API boundaries and explicit states; Task 5 delivers React reading pages; Task 6 provides separate deployment entry points. Stock detail, industry, portfolio, historical validation, data status, administrator migration, and Streamlit retirement remain intentionally separate plans because each carries an independently reviewable API and UX boundary.

No-placeholder check: this plan contains no unresolved placeholder markers and gives exact files, test names, commands, expected red/green outcomes, and interface names. Type check: Python adapters return dictionaries validated by Pydantic; API JSON uses `status`, `reason`, nullable numeric fields and pagination fields consistently; React consumes only those API DTOs.
