import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { DashboardShell } from "@/components/dashboard-shell";
import { SignOutButton } from "@/components/sign-out-button";
import { hasAppAccess, insecureLocalAccessEnabled } from "@/lib/access";
import { loadDashboardData } from "@/lib/dashboard-data";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  if (!await hasAppAccess()) redirect("/sign-in");
  const localAccess = insecureLocalAccessEnabled();
  const session = localAccess ? null : await auth();
  let data: Awaited<ReturnType<typeof loadDashboardData>> | null = null;
  let loadError: string | null = null;

  try {
    data = await loadDashboardData();
  } catch (error) {
    loadError = error instanceof Error ? error.message : "分析APIへ接続できませんでした";
  }
  if (!data) {
    return (
      <main className="startup-error">
        <div className="brand-mark" aria-hidden="true">灯</div>
        <span className="eyebrow">TOMOSHIBIYORI</span>
        <h1>分析APIを準備しています</h1>
        <p>{loadError}</p>
        <code>docker compose up --build frontend</code>
      </main>
    );
  }
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-mark" aria-hidden="true">灯</div>
        <div className="brand-copy">
          <h1>TOMOSHIBIYORI</h1>
          <p>日足から、明日の判断に小さな灯を。</p>
        </div>
        <div className="session-area">
          <span>{localAccess ? "ローカル開発モード" : session?.user?.name ?? "本人認証済み"}</span>
          {!localAccess && <SignOutButton />}
        </div>
      </header>
      <DashboardShell data={data} />
      <footer className="app-footer">
        本画面は分析情報を提供するもので、投資助言や利益保証ではありません。
      </footer>
    </div>
  );
}
