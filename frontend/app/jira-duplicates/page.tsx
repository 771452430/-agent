/**
 * Jira 重复工单审核 Agent 页面路由。
 */
import { JiraDuplicateAgentsWorkspace } from "../../components/jira-duplicate-agents-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function JiraDuplicatesPage() {
  return (
    <WorkbenchShell>
      <JiraDuplicateAgentsWorkspace />
    </WorkbenchShell>
  );
}
