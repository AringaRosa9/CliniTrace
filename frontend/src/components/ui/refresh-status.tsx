"use client";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
export function RefreshStatus() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      className="refresh-status"
      disabled={pending}
      onClick={() => startTransition(() => router.refresh())}
    >
      {pending ? "正在检查…" : "刷新状态 ↗"}
    </button>
  );
}
