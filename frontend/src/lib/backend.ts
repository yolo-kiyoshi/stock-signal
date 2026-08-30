const API_PREFIX = "/api/v1/";
const LOCAL_API_TOKEN = "change-this-local-token-before-deploying";

function backendConfiguration(): { baseUrl: string; token: string } {
  const baseUrl = process.env.FASTAPI_INTERNAL_URL?.replace(/\/$/, "");
  const token = process.env.INTERNAL_API_TOKEN;
  if (!baseUrl) throw new Error("FASTAPI_INTERNAL_URLが設定されていません");
  if (!token || token.length < 32) {
    throw new Error("INTERNAL_API_TOKENは32文字以上で設定してください");
  }
  if (process.env.NODE_ENV === "production" && token === LOCAL_API_TOKEN) {
    throw new Error("production用のINTERNAL_API_TOKENが設定されていません");
  }
  return { baseUrl, token };
}

function validateApiPath(path: string): void {
  if (!path.startsWith(API_PREFIX) || path.includes("..") || path.includes("://")) {
    throw new Error("許可されていないAPIパスです");
  }
}

export async function backendFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  validateApiPath(path);
  const { baseUrl, token } = backendConfiguration();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", "application/json");
  return fetch(`${baseUrl}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
}

export async function backendJson<T>(path: string): Promise<T> {
  const response = await backendFetch(path);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(payload.detail ?? `APIの取得に失敗しました（${response.status}）`);
  }
  return response.json() as Promise<T>;
}
