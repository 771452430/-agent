import { JiraSolutionSearchWorkspace } from "../../components/jira-solution-search-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function JiraSolutionSearchPage() {
  return (
    <WorkbenchShell>
      <JiraSolutionSearchWorkspace />
    </WorkbenchShell>
  );
}
