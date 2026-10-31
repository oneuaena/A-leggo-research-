"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { RefreshCw, Smartphone, X } from "@/shared/ui/glyphs";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/shared/api/client";

type PairInfo = { name: string; host: string; port: number; token: string };

/**
 * Shows the LAN pairing QR code. The phone scans it once; afterwards it
 * reconnects automatically from its stored bundle.
 */
export function PhonePairDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<PairInfo | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await apiFetch("/api/aleggo/pair/info", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      setInfo((await res.json()) as PairInfo);
      setError("");
    } catch {
      setError("读取配对信息失败，请重试");
    }
  }, []);

  const rotate = useCallback(async () => {
    try {
      const res = await apiFetch("/api/aleggo/pair/rotate", { method: "POST" });
      if (!res.ok) throw new Error(String(res.status));
      setInfo((await res.json()) as PairInfo);
      setError("");
    } catch {
      setError("刷新配对码失败，请重试");
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(44,35,24,0.42)] p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-[20px] border border-[var(--border)] bg-[var(--card)] p-6 shadow-[0_18px_50px_rgba(96,72,44,0.2)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 font-serif text-lg">
            <Smartphone size={17} className="text-[var(--primary)]" /> 连接手机
          </h2>
          <ActionButton
            onClick={onClose}
            aria-label="关闭"
            className="rounded-full p-1.5 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <X size={16} />
          </ActionButton>
        </div>
        {info ? (
          <div className="mt-4 flex flex-col items-center gap-3">
            <img
              src={`/api/aleggo/pair/qr.svg?t=${encodeURIComponent(info.token)}`}
              alt="手机配对二维码"
              className="h-56 w-56 rounded-[14px] border border-[var(--border)] bg-white p-2"
            />
            <p className="text-[13px] font-semibold text-[var(--foreground)]">
              本机：{info.name} · {info.host}:{info.port}
            </p>
            <p className="text-center text-[12px] leading-relaxed text-[var(--muted-foreground)]">
              手机打开 A-leggo，点「扫描电脑二维码」，扫此码即自动完成配对；
              以后打开 App 会直接连上，无需再配置。
            </p>
            <ActionButton
              onClick={() => void rotate()}
              className="inline-flex items-center gap-1.5 rounded-[11px] border border-[rgba(255,220,190,0.3)] bg-[linear-gradient(155deg,var(--wax,#c25a38),var(--wax-deep,#a04425))] px-4 py-2 text-[12.5px] font-semibold text-[#fdf6e9] shadow-[0_6px_18px_rgba(168,74,38,0.22)] transition hover:brightness-[1.07]"
            >
              <RefreshCw size={13.5} /> 刷新配对码
            </ActionButton>
          </div>
        ) : (
          <p className="mt-6 text-center text-[13px] text-[var(--muted-foreground)]">
            {error || "正在生成配对码…"}
          </p>
        )}
        {error && info ? (
          <p className="mt-2 text-center text-[12px] text-[var(--destructive)]">{error}</p>
        ) : null}
      </div>
    </div>
  );
}
