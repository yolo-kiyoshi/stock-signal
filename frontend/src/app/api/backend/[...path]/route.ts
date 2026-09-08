import type { NextRequest } from "next/server";

import { hasAppAccess } from "@/lib/access";
import { backendFetch } from "@/lib/backend";

type RouteContext = { params: Promise<{ path: string[] }> };

function firstForwardedValue(value: string | null): string | null {
  const first = value?.split(",", 1)[0]?.trim();
  return first || null;
}

function buildHttpOrigin(protocol: string, host: string): string | null {
  const normalizedProtocol = protocol.replace(/:$/, "").toLowerCase();
  if (!["http", "https"].includes(normalizedProtocol)) return null;

  try {
    const url = new URL(`${normalizedProtocol}://${host}`);
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
      return null;
    }
    return url.origin;
  } catch {
    return null;
  }
}

function isSamePublicOrigin(request: NextRequest): boolean {
  const originHeader = request.headers.get("origin");
  if (!originHeader) return false;

  let requestOrigin: string;
  try {
    const originUrl = new URL(originHeader);
    if (
      !["http:", "https:"].includes(originUrl.protocol)
      || originUrl.username
      || originUrl.password
      || originUrl.pathname !== "/"
      || originUrl.search
      || originUrl.hash
    ) {
      return false;
    }
    requestOrigin = originUrl.origin;
  } catch {
    return false;
  }

  const acceptedOrigins = new Set<string>([request.nextUrl.origin]);
  const forwardedProtocol = firstForwardedValue(request.headers.get("x-forwarded-proto"));
  const publicProtocol = forwardedProtocol ?? request.nextUrl.protocol;
  const publicHosts = [
    request.headers.get("host"),
    firstForwardedValue(request.headers.get("x-forwarded-host")),
  ];
  for (const host of publicHosts) {
    if (!host) continue;
    const origin = buildHttpOrigin(publicProtocol, host);
    if (origin) acceptedOrigins.add(origin);
  }
  return acceptedOrigins.has(requestOrigin);
}

async function forward(request: NextRequest, context: RouteContext): Promise<Response> {
  if (!["GET", "HEAD"].includes(request.method)) {
    if (!isSamePublicOrigin(request)) {
      return Response.json({ detail: "同一オリジンから実行してください" }, { status: 403 });
    }
  }
  if (!await hasAppAccess()) {
    return Response.json({ detail: "ログインが必要です" }, { status: 401 });
  }

  const { path } = await context.params;
  if (!path.length || path.some((part) => !/^[a-zA-Z0-9._-]+$/.test(part))) {
    return Response.json({ detail: "APIパスが不正です" }, { status: 400 });
  }
  const query = request.nextUrl.search;
  const apiPath = `/api/v1/${path.map(encodeURIComponent).join("/")}${query}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : await request.arrayBuffer();

  try {
    const upstream = await backendFetch(apiPath, {
      method: request.method,
      headers,
      body,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "分析APIへ接続できませんでした" },
      { status: 502 },
    );
  }
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
