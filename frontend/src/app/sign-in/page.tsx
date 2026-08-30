import { redirect } from "next/navigation";

import { signIn } from "@/auth";
import { insecureLocalAccessEnabled } from "@/lib/access";

export default function SignInPage() {
  if (insecureLocalAccessEnabled()) redirect("/");
  return (
    <main className="sign-in-page">
      <section className="sign-in-card">
        <div className="brand-mark" aria-hidden="true">灯</div>
        <span className="eyebrow">PRIVATE INVESTMENT COMPANION</span>
        <h1>TOMOSHIBIYORI</h1>
        <p>このアプリは所有者本人だけが利用できます。</p>
        <form
          action={async () => {
            "use server";
            await signIn("github", { redirectTo: "/" });
          }}
        >
          <button className="primary-button" type="submit">GitHubで本人確認</button>
        </form>
        <small>許可済みのGitHubユーザーID以外はログインできません。</small>
      </section>
    </main>
  );
}
