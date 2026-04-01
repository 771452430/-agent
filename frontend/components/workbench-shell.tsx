"use client";

/**
 * 工作台公共外壳。
 *
 * 它负责左侧导航、全局设置入口和右侧主体内容区域，是所有功能页共享的骨架。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { FeishuSettingsPanel } from "./feishu-settings-panel";
import { GitLabImportSettingsPanel } from "./gitlab-import-settings-panel";
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
  const { openFeishuSettings, openGitLabImportSettings, openMailSettings, openModelSettings } = useModelSettings();

  useEffect(() => {
    setIsUtilityMenuOpen(false);
  }, [pathname]);

  return (
    <div className="min-h-screen px-4 py-4 md:px-6 md:py-6">
      <div className="apple-window grid min-h-[calc(100vh-2rem)] overflow-hidden rounded-[34px] lg:h-[calc(100vh-3rem)] lg:min-h-0 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="apple-sidebar relative flex min-h-0 flex-col overflow-hidden border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="mb-6 flex items-center gap-2">
              <span className="h-3 w-3 rounded-full bg-[#ff5f57]" />
              <span className="h-3 w-3 rounded-full bg-[#febc2e]" />
              <span className="h-3 w-3 rounded-full bg-[#28c840]" />
            </div>

            <div className="apple-panel rounded-[28px] p-5">
              <div className="apple-kicker">Learning Studio</div>
              <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-white">RAG Workbench</h1>
              <p className="mt-3 text-sm leading-6 text-slate-300">
                把聊天、检索、配置型 Agent 和自动化工作流收进一个更轻、更清晰的学习工作台。
              </p>
            </div>

            <nav className="mt-6 space-y-3">
              {NAV_ITEMS.map((item) => {
                const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={
                      "apple-nav-link block rounded-[24px] px-4 py-4 transition " +
                      (isActive ? "apple-nav-link-active" : "")
                    }
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-medium text-white">{item.label}</div>
                      {isActive && <span className="apple-pill rounded-full px-2.5 py-1 text-[11px] text-sky-100">当前</span>}
                    </div>
                    <div className="mt-1.5 text-sm leading-6 text-slate-400">{item.description}</div>
                  </Link>
                );
              })}
            </nav>

            <div className="apple-panel-subtle mt-6 rounded-[26px] p-4 text-sm text-slate-300">
              <div className="font-medium text-white">推荐学习路径</div>
              <div className="mt-2 leading-6">
                先在 Chat 里观察 Skill 路由，再去检索模式体验 scoped RAG，然后配置 Agent，最后学习巡检与支持问题链路。
              </div>
              <Link href="/catalog" className="mt-4 inline-flex items-center text-sm text-sky-200">
                查看 Skill Catalog
              </Link>
            </div>
          </div>

          <div className="relative mt-auto pt-6">
          {isUtilityMenuOpen && (
              <div className="apple-panel-strong absolute inset-x-0 bottom-16 rounded-[28px] p-2.5">
              <button
                  className="apple-button-ghost w-full rounded-[20px] px-3.5 py-3.5 text-left text-sm"
                onClick={() => {
                  openModelSettings();
                  setIsUtilityMenuOpen(false);
                }}
              >
                  <div className="font-medium text-white">模型设置</div>
                  <div className="mt-1 text-xs leading-5 text-slate-400">统一管理厂商、协议、API Key 和模型列表</div>
              </button>
              <button
                  className="apple-button-ghost mt-2 w-full rounded-[20px] px-3.5 py-3.5 text-left text-sm"
                onClick={() => {
                  openMailSettings();
                  setIsUtilityMenuOpen(false);
                }}
              >
                  <div className="font-medium text-white">邮箱设置</div>
                  <div className="mt-1 text-xs leading-5 text-slate-400">统一管理 SMTP、发件邮箱和测试发信</div>
              </button>
              <button
                  className="apple-button-ghost mt-2 w-full rounded-[20px] px-3.5 py-3.5 text-left text-sm"
                onClick={() => {
                  openFeishuSettings();
                  setIsUtilityMenuOpen(false);
                }}
              >
                  <div className="font-medium text-white">飞书设置</div>
                  <div className="mt-1 text-xs leading-5 text-slate-400">统一管理支持问题 Agent 使用的飞书应用凭据</div>
              </button>
              <button
                  className="apple-button-ghost mt-2 w-full rounded-[20px] px-3.5 py-3.5 text-left text-sm"
                onClick={() => {
                  openGitLabImportSettings();
                  setIsUtilityMenuOpen(false);
                }}
              >
                  <div className="font-medium text-white">GitLab 导入设置</div>
                  <div className="mt-1 text-xs leading-5 text-slate-400">统一管理检索模式导入 GitLab 文档树使用的 Token 和域名白名单</div>
              </button>
            </div>
          )}

          <button
              className="apple-panel flex w-full items-center justify-between rounded-[26px] px-4 py-3.5 text-left"
            onClick={() => setIsUtilityMenuOpen((current) => !current)}
          >
            <div>
                <div className="text-sm font-medium text-white">工作台设置</div>
                <div className="mt-1 text-xs leading-5 text-slate-400">模型、邮箱、飞书和全局接入配置</div>
            </div>
              <div className="apple-pill rounded-full px-3 py-1 text-xs">{isUtilityMenuOpen ? "收起" : "展开"}</div>
          </button>
        </div>
      </aside>

        <main className="min-w-0 bg-white/[0.02] lg:min-h-0 lg:overflow-hidden">{props.children}</main>
      </div>
      <ModelSettingsPanel />
      <MailSettingsPanel />
      <FeishuSettingsPanel />
      <GitLabImportSettingsPanel />
    </div>
  );
}
