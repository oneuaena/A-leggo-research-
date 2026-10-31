import { NextResponse } from "next/server";

import { resolveBackendApiBase } from "@/lib/backend-runtime-config";
import { apiFetch } from "@/shared/api/client";

// Phones probe `<server>/health` right after scanning the pairing QR code.
// The middleware rewrite covers it too; this handler keeps the path alive
// even when the edge rewrite is skipped by a cached routing decision.
export const dynamic = "force-dynamic";

async function probe(suffix: string) {
  try {
    const res = await apiFetch(`${resolveBackendApiBase()}${suffix}`, {
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    return NextResponse.json(body, { status: res.status });
  } catch {
    return NextResponse.json({ status: "unreachable" }, { status: 502 });
  }
}

export async function GET() {
  return probe("/health");
}
