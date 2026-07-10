import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend-fetch";
import { getAccessToken } from "@/lib/auth-cookies";

export const runtime = "nodejs";

export async function GET() {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  const response = await backendFetch("/workspaces", { accessToken });
  const body = await response.json().catch(() => []);
  return NextResponse.json(body, { status: response.status });
}
