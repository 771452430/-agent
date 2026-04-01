"use client";

/**
 * 前端全局 Provider 入口。
 *
 * 目前主要负责挂载模型设置上下文，后续新增更多全局上下文时也会从这里继续包裹。
 */
import type { ReactNode } from "react";

import { ModelSettingsProvider } from "../components/model-settings-provider";

export function Providers({ children }: { children: ReactNode }) {
  return <ModelSettingsProvider>{children}</ModelSettingsProvider>;
}
