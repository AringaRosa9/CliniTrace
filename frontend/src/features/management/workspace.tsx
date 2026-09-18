"use client";
import type { Props } from "./shared";
import { Templates } from "./templates";
import { Terminology } from "./terminology";
import { Quality } from "./quality";
import "./workspace.css";
export function ManagementWorkspace({
  mode,
  ...props
}: Props & { mode: "templates" | "terminology" | "quality" }) {
  return (
    <main id="main" className="documents-main management-workspace">
      {mode === "templates" ? (
        <Templates {...props} />
      ) : mode === "terminology" ? (
        <Terminology {...props} />
      ) : (
        <Quality {...props} />
      )}
    </main>
  );
}
