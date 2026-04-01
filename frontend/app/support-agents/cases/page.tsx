/**
 * 支持问题案例候选页面路由。
 *
 * 这个入口专门用于查看和审核支持问题 Agent 自动沉淀出的案例候选。
 */
import { Suspense } from "react";

import { SupportIssueCaseCandidatesWorkspace } from "../../../components/support-issue-case-candidates-workspace";
import { WorkbenchShell } from "../../../components/workbench-shell";

export default function SupportAgentCasesPage() {
  return (
    <WorkbenchShell>
      <Suspense
        fallback={
          <div className="min-h-screen bg-slate-950 px-6 py-6 text-slate-400">
            正在加载案例候选池...
          </div>
        }
      >
        <SupportIssueCaseCandidatesWorkspace />
      </Suspense>
    </WorkbenchShell>
  );
}
