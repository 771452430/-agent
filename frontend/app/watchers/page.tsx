/**
 * 巡检 Agent 页面路由。
 *
 * 页面文件只做组合；抓取配置、规则编辑和运行历史都交给工作区组件处理。
 */
import { WatchersWorkspace } from "../../components/watchers-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function WatchersPage() {
  return (
    <WorkbenchShell>
      <WatchersWorkspace />
    </WorkbenchShell>
  );
}
