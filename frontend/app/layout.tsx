/**
 * Next.js 根布局。
 *
 * 这里决定整个前端应用最外层的 HTML 结构，以及全局 Provider 的挂载位置。
 */
import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "LangChain Learning Demo",
  description: "边做边学的 LangChain + RAG + Skill 学习型平台"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
