import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { StockSearch } from "../components/StockSearch";
import { getCapabilities } from "../api/admin";
import { AdminPage } from "../features/admin/AdminPage";

import { CandidatePoolPage } from "../features/research/CandidatePoolPage";
import { ResearchHomePage } from "../features/research/ResearchHomePage";
import { StockDetailPage } from "../features/research/StockDetailPage";
import { IndustryPage } from "../features/research/IndustryPage";
import { HistoricalValidationPage } from "../features/validation/HistoricalValidationPage";
import { DataStatusPage } from "../features/status/DataStatusPage";
import { PortfolioPage } from "../features/portfolio/PortfolioPage";

export function App() {
  const navigate = useNavigate();
  const [adminEnabled, setAdminEnabled] = useState(false);
  useEffect(() => { getCapabilities().then((result) => setAdminEnabled(result.admin_enabled)).catch(() => setAdminEnabled(false)); }, []);
  return (
    <>
      <nav aria-label="研究导航"><Link className="brand" to="/">研股工作台</Link><div className="nav-links"><NavLink end to="/">工作台</NavLink><NavLink to="/candidates">选股</NavLink><NavLink to="/portfolios">我的组合</NavLink><NavLink to="/validation">历史回测实验</NavLink></div><StockSearch label="全站股票搜索" onSelect={(stock) => navigate(`/stocks/${encodeURIComponent(stock.code)}`)} /><Link className="utility-link" to="/data-status">数据状态</Link>{adminEnabled && <Link className="utility-link" to="/admin">管理员</Link>}</nav>
      <Routes>
        <Route path="/" element={<ResearchHomePage />} />
        <Route path="/candidates" element={<CandidatePoolPage />} />
        <Route path="/stocks" element={<StockDetailPage />} />
        <Route path="/stocks/:code" element={<StockDetailPage />} />
        <Route path="/industries" element={<IndustryPage />} />
        <Route path="/portfolios" element={<PortfolioPage />} /><Route path="/portfolios/:portfolioId" element={<PortfolioPage />} />
        <Route path="/validation" element={<HistoricalValidationPage />} />
        <Route path="/data-status" element={<DataStatusPage />} />
        {adminEnabled && <Route path="/admin" element={<AdminPage />} />}
      </Routes>
    </>
  );
}
