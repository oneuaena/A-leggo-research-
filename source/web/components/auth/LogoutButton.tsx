"use client";

import { ActionButton } from "@/shared/ui/ActionButton";
import { LogOut } from "@/shared/ui/glyphs";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { logout } from "@/lib/auth";
import { useAuthStatus } from "@/hooks/useAuthStatus";

interface LogoutButtonProps {
  collapsed?: boolean;
}

export function LogoutButton({ collapsed = false }: LogoutButtonProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const { enabled } = useAuthStatus();

  if (!enabled) return null;

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (collapsed) {
    return (
      <ActionButton
        onClick={handleLogout}
        className="rounded-lg p-2 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/50 hover:text-red-500"
        aria-label={t("Sign out")}
        title={t("Sign out")}
      >
        <LogOut size={16} strokeWidth={1.5} />
      </ActionButton>
    );
  }

  return (
    <ActionButton
      onClick={handleLogout}
      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/50 hover:text-red-500"
    >
      <LogOut size={16} strokeWidth={1.5} />
      <span>{t("Sign out")}</span>
    </ActionButton>
  );
}
