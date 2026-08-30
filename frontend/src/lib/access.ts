import { auth } from "@/auth";

const LOCAL_AUTH_SECRET = "change-this-auth-secret-before-deploying";

export function assertSecureAuthConfiguration(): void {
  if (process.env.NODE_ENV !== "production") return;

  const secret = process.env.AUTH_SECRET;
  if (!secret || secret.length < 32 || secret === LOCAL_AUTH_SECRET) {
    throw new Error("production用のAUTH_SECRETが設定されていません");
  }
  if (!process.env.AUTH_GITHUB_ID || !process.env.AUTH_GITHUB_SECRET) {
    throw new Error("production用のGitHub OAuth設定がありません");
  }
  if (!process.env.AUTH_ALLOWED_GITHUB_ID?.trim()) {
    throw new Error("許可するGitHubユーザーIDが設定されていません");
  }
}

export function insecureLocalAccessEnabled(): boolean {
  return (
    process.env.NODE_ENV !== "production"
    && process.env.AUTH_ALLOW_INSECURE_LOCAL === "true"
  );
}

export async function hasAppAccess(): Promise<boolean> {
  assertSecureAuthConfiguration();
  if (insecureLocalAccessEnabled()) return true;
  const session = await auth();
  return Boolean(session?.user);
}
