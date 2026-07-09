import { createBrowserRouter, Navigate, Outlet } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Layout } from "@/components/layout/Layout";
import { DailyReview } from "@/pages/DailyReview";
import { Intel } from "@/pages/Intel";
import { Sectors } from "@/pages/Sectors";
import { SectorDetail } from "@/pages/SectorDetail";
import { Portfolio } from "@/pages/Portfolio";
import { StockData } from "@/pages/StockData";
import { Watchlist } from "@/pages/Watchlist";
import { MyReports } from "@/pages/MyReports";
import { Notes } from "@/pages/Notes";
import { Agent } from "@/pages/Agent";
import { Settings } from "@/pages/Settings";

export const router = createBrowserRouter([
  {
    // 顶层：始终显示顶部 Tab 栏
    element: <AppShell />,
    children: [
      {
        // 股神分支：全屏，无侧栏
        path: "/agent",
        element: <Outlet />,
        children: [
          { index: true, element: <Agent /> },
        ],
      },
      {
        // 个人分支：现有 Layout（侧栏 + 主区）
        element: <Layout />,
        children: [
          { path: "/", element: <Navigate to="/daily-review" replace /> },
          { path: "/daily-review", element: <DailyReview /> },
          { path: "/intel", element: <Intel /> },
          { path: "/sectors", element: <Sectors /> },
          { path: "/sectors/:key", element: <SectorDetail /> },
          { path: "/portfolio", element: <Portfolio /> },
          { path: "/stock-data", element: <StockData /> },
          { path: "/watchlist", element: <Watchlist /> },
          { path: "/my-reports", element: <MyReports /> },
          { path: "/notes", element: <Notes /> },
          { path: "/settings", element: <Settings /> },
        ],
      },
    ],
  },
]);
