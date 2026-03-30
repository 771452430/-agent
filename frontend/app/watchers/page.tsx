import { WatchersWorkspace } from "../../components/watchers-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function WatchersPage() {
  return (
    <WorkbenchShell>
      <WatchersWorkspace />
    </WorkbenchShell>
  );
}
