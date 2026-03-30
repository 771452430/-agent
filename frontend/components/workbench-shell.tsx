"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import type { ReactNode } from "react";

import { FeishuSettingsPanel } from "./feishu-settings-panel";
import { MailSettingsPanel } from "./mail-settings-panel";
import { ModelSettingsPanel } from "./model-settings-panel";
import { useModelSettings } from "./model-settings-provider";

const NAV_ITEMS = [
  { href: "/", label: "Chat", description: "多轮对话 + Skill" },
  { href: "/retrieval", label: "检索模式", description: "知识树 + Scoped RAG" },
  { href: "/agents", label: "我的 Agent", description: "配置型 Agent" },
  { href: "/watchers", label: "巡检 Agent", description: "LangGraph 轮巡分配通知" },
  { href: "/support-agents", label: "支持问题 Agent", description: "飞书多维表格 + RAG 回写" }
];

export function WorkbenchShell(props: { children: ReactNode }) {
  const pathname = usePathname();
  const [isUtilityMenuOpen, setIsUtilityMenuOpen] = useState(false);
  const { openFeishuSettings, openMailSettings, openModelSettings } = useModelSettings();

  return (
    <div className="grid min-h-screen grid-cols-[220px_minmax(0,1fr)] bg-slate-950 text-slate-100">
      <aside className="flex min-h-screen flex-col border-r border-slate-800 bg-slate-900/80 p-5">
        <div>
          <div className="text-xs uppercase tracking-[0.35em] text-amber-300">RAG Workbench</div>
          <h1 className="mt-3 text-2xl font-semibold">Learning Studio</h1>
          <p className="mt-3 text-sm leading-6 text-slate-400">把聊天、检索范围和自定义 Agent 放到同一个学习型工作台里。</p>
        </div>

        <nav className="mt-8 space-y-3">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={
                  "block rounded-2xl border px-4 py-4 transition " +
                  (isActive
                    ? "border-amber-300/60 bg-amber-300/10"
                    : "border-slate-800 bg-slate-950/40 hover:border-slate-700")
                }
              >
                <div className="font-medium">{item.label}</div>
                <div className="mt-1 text-sm text-slate-400">{item.description}</div>
              </Link>
            );
          })}
        </nav>

        <div className="mt-8 rounded-2xl border border-slate-800 bg-slate-950/50 p-4 text-sm text-slate-400">
          <div className="font-medium text-slate-200">学习路径</div>
          <div className="mt-2 leading-6">建议顺序：先在 Chat 里看 Skill 触发，再去检索模式体验 scoped RAG，然后配置我的 Agent，最后学习巡检 Agent 的自动化链路。</div>
          <Link href="/catalog" className="mt-4 inline-block text-amber-300">
            查看 Skill Catalog
          </Link>
        </div>

        <div className="relative mt-auto pt-6">
          {isUtilityMenuOpen && (
            <div className="absolute inset-x-0 bottom-16 rounded-2xl border border-slate-800 bg-slate-950/95 p-2 shadow-xl shadow-black/30">
              <button
                className="w-full rounded-xl px-3 py-3 text-left text-sm text-slate-200 transition hover:bg-slate-900"
                onClick={() => {
                  openModelSettings();
                  setIsUtilityMenuOpen(false);
                }}
              >
                <div className="font-medium">模型设置</div>
                <div className="mt-1 text-xs text-slate-500">统一管理厂商、协议、API Key 和模型列表</div>
              </button>
              <button
                className="mt-2 w-full rounded-xl px-3 py-3 text-left text-sm text-slate-200 transition hover:bg-slate-900"
                onClick={() => {
                  openMailSettings();
                  setIsUtilityMenuOpen(false);
                }}
                >
                  <div className="font-medium">邮箱设置</div>
                  <div className="mt-1 text-xs text-slate-500">统一管理 SMTP、发件邮箱和测试发信</div>
                </button>
              <button
                className="mt-2 w-full rounded-xl px-3 py-3 text-left text-sm text-slate-200 transition hover:bg-slate-900"
                onClick={() => {
                  openFeishuSettings();
                  setIsUtilityMenuOpen(false);
                }}
              >
                <div className="font-medium">飞书设置</div>
                <div className="mt-1 text-xs text-slate-500">统一管理支持问题 Agent 使用的飞书应用凭据</div>
              </button>
            </div>
          )}

          <button
            className="flex w-full items-center justify-between rounded-2xl border border-slate-800 bg-slate-950/80 px-4 py-3 text-left transition hover:border-slate-700"
            onClick={() => setIsUtilityMenuOpen((current) => !current)}
          >
            <div>
              <div className="text-sm font-medium text-slate-100">设置</div>
              <div className="mt-1 text-xs text-slate-500">模型设置、邮箱设置等全局配置</div>
            </div>
            <div className="text-slate-500">{isUtilityMenuOpen ? "收起" : "展开"}</div>
          </button>
        </div>
      </aside>

      <main className="min-w-0">{props.children}</main>
      <ModelSettingsPanel />
      <MailSettingsPanel />
      <FeishuSettingsPanel />
    </div>
  );
}
