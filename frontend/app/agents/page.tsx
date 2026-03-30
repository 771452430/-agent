import { AgentsWorkspace } from "../../components/agents-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function AgentsPage() {
  return (
    <WorkbenchShell>
      <AgentsWorkspace />
    </WorkbenchShell>
  );
}
