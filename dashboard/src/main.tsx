import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { AuditPage } from "./pages/Audit";
import { CaseDetailPage } from "./pages/CaseDetail";
import { CasesPage } from "./pages/Cases";
import { GovernancePage } from "./pages/Governance";
import { LoginPage } from "./pages/Login";
import { NewInvestigationPage } from "./pages/NewInvestigation";
import { OverviewPage } from "./pages/Overview";
import { ReviewQueuePage } from "./pages/ReviewQueue";
import { SourcesPage } from "./pages/Sources";
import "./styles.css";

function Protected() {
  const { session } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/investigations/new" element={<NewInvestigationPage />} />
        <Route path="/cases" element={<CasesPage />} />
        <Route path="/cases/:caseId" element={<CaseDetailPage />} />
        <Route path="/reviews" element={<ReviewQueuePage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/governance" element={<GovernancePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/*" element={<Protected />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
);
