"use client";

import type { ReactNode } from "react";

import { ModelSettingsProvider } from "../components/model-settings-provider";

export function Providers({ children }: { children: ReactNode }) {
  return <ModelSettingsProvider>{children}</ModelSettingsProvider>;
}
