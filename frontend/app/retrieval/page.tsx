/**
 * 检索模式页面路由。
 *
 * 路由层只负责组合壳层和检索工作区，让知识树/RAG 交互集中在组件层。
 */
import { RetrievalWorkspace } from "../../components/retrieval-workspace";
import { WorkbenchShell } from "../../components/workbench-shell";

export default function RetrievalPage() {
  return (
    <WorkbenchShell>
      <RetrievalWorkspace />
    </WorkbenchShell>
  );
}
