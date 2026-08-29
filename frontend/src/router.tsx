import { createBrowserRouter, Navigate, useParams, type RouteObject } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DailyReview } from "@/pages/DailyReview";
import { Intel } from "@/pages/Intel";
import { Signals } from "@/pages/Signals";
import { Sectors } from "@/pages/Sectors";
import { SectorDetail } from "@/pages/SectorDetail";
import { Debate } from "@/pages/Debate";
import { Portfolio } from "@/pages/Portfolio";
import { StockData } from "@/pages/StockData";
import { Watchlist } from "@/pages/Watchlist";
import { MyReports } from "@/pages/MyReports";
import { Notes } from "@/pages/Notes";
import { Settings } from "@/pages/Settings";
import { SettingsLayout } from "@/pages/SettingsLayout";
import { Agent } from "@/pages/Agent";
import { Skills } from "@/pages/Skills";
import { SkillDetail } from "@/pages/SkillDetail";

/** 旧路径兼容重定向：按路由参数拼出新地址。 */
function Redirect({ to }: { to: (params: Record<string, string | undefined>) => string }) {
  const params = useParams();
  return <Navigate to={to(params)} replace />;
}

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to="/daily-review" replace /> },
      { path: "/daily-review", element: <DailyReview /> },
      { path: "/intel", element: <Intel /> },
      { path: "/intel/:tab", element: <Intel /> },
      { path: "/signals", element: <Signals /> },
      { path: "/signals/:tab", element: <Signals /> },
      { path: "/sectors", element: <Sectors /> },
      { path: "/sectors/:key", element: <SectorDetail /> },
      { path: "/portfolio", element: <Portfolio /> },
      { path: "/stock-data", element: <StockData /> },
      { path: "/debate", element: <Debate /> },
      { path: "/watchlist", element: <Watchlist /> },
      { path: "/my-reports", element: <MyReports /> },
      { path: "/notes", element: <Notes /> },
      {
        path: "/settings",
        element: <SettingsLayout />,
        children: [
          { index: true, element: <Navigate to="/settings/model" replace /> },
          { path: "model", element: <Settings /> },
          { path: "skills", element: <Skills /> },
          { path: "skills/:source/:name", element: <SkillDetail /> },
        ],
      },
      { path: "/agent", element: <Agent /> },
      { path: "/skills", element: <Navigate to="/settings/skills" replace /> },
      {
        path: "/skills/:source/:name",
        element: <Redirect to={(params) => `/settings/skills/${params.source}/${params.name}`} />,
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
