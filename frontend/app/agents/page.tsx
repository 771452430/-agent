/**
 * 配置型 Agent 页面路由。
 *
 * 路由层本身保持很薄，只负责把真正复杂的工作区组件挂到统一工作台里。
 */
import { AgentsWorkspace } from "../../components/agents-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function AgentsPage() {
  return (
    <WorkbenchShell>
      <AgentsWorkspace />
    </WorkbenchShell>
  );
}
