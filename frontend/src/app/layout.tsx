import type { Metadata } from "next";
import "../styles/globals.css";
export const metadata: Metadata = {
  title: "临床数据结构化平台",
  description: "有原文依据、可审核、可追溯的临床数据工作空间",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
