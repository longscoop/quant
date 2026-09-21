import { Link, Route, Routes } from "react-router-dom";

import { CandidatePoolPage } from "../features/research/CandidatePoolPage";
import { ResearchHomePage } from "../features/research/ResearchHomePage";

export function App() {
  return (
    <>
      <nav aria-label="研究导航"><Link to="/">研究首页</Link> · <Link to="/candidates">候选池</Link></nav>
      <Routes>
        <Route path="/" element={<ResearchHomePage />} />
        <Route path="/candidates" element={<CandidatePoolPage />} />
      </Routes>
    </>
  );
}
