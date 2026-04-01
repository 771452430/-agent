/**
 * 支持问题 Agent 页面路由。
 *
 * 路由层负责承载工作台，复杂的飞书接入、字段映射和运行结果都在工作区组件里。
 */
import { SupportIssueAgentsWorkspace } from "../../components/support-issue-agents-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function SupportAgentsPage() {
  return (
    <WorkbenchShell>
      <SupportIssueAgentsWorkspace />
    </WorkbenchShell>
  );
}
