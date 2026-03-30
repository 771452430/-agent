import { ChatWorkspace } from "../components/chat-workspace";
import { WorkbenchShell } from "../components/workbench-shell";

export default function HomePage() {
  return (
    <WorkbenchShell>
      <ChatWorkspace />
    </WorkbenchShell>
  );
}
