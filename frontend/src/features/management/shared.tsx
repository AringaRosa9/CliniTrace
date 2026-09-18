"use client";
import { useRef, useState, type ReactNode, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { components } from "@/lib/api/schema";
export type Models = components["schemas"];
export type Props = { project: Models["ProjectView"]; csrf: string };
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="management-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export function Json({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label}>
      <textarea
        spellCheck={false}
        rows={10}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}
export function SubmitForm({
  children,
  submit,
  label,
  disabled = false,
}: {
  children: ReactNode;
  submit: (form: FormData) => Promise<unknown>;
  label: string;
  disabled?: boolean;
}) {
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const [message, setMessage] = useState("");
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (guard.current) return;
    guard.current = true;
    setBusy(true);
    setMessage("");
    try {
      await submit(new FormData(e.currentTarget));
      await cache.invalidateQueries({ queryKey: ["project"] });
      setMessage("已保存，可在列表中核对。");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "操作失败，请检查输入。");
    } finally {
      guard.current = false;
      setBusy(false);
    }
  }
  return (
    <form onSubmit={save} className="management-form">
      <fieldset disabled={busy || disabled}>
        {children}
        <button className="primary" type="submit">
          {busy ? "正在保存…" : label}
        </button>
      </fieldset>
      <p role="status">{message}</p>
    </form>
  );
}
export function RecordDetails({
  title,
  value,
}: {
  title: string;
  value: unknown;
}) {
  return (
    <details>
      <summary>{title}</summary>
      <pre className="management-json">{pretty(value)}</pre>
    </details>
  );
}
export function useActivity() {
  const tracker = useRef({ last: 0, seconds: 0 });
  return {
    tick: () => {
      const t = Date.now();
      const delta = (t - tracker.current.last) / 1000;
      if (delta > 0 && delta <= 60) tracker.current.seconds += delta;
      tracker.current.last = t;
    },
    seconds: () => Math.round(tracker.current.seconds),
    reset: () => {
      tracker.current = { last: 0, seconds: 0 };
    },
  };
}
