/**
 * Chat 首页路由。
 *
 * 页面文件本身非常薄，只负责把 `ChatWorkspace` 放进统一工作台壳子里。
 */
import { ChatWorkspace } from "../components/chat-workspace";
import { WorkbenchShell } from "../components/workbench-shell";

export default function HomePage() {
  return (
    <WorkbenchShell>
      <ChatWorkspace />
    </WorkbenchShell>
  );
}
