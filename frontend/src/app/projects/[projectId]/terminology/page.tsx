import { DocumentWorkspace } from "@/features/documents/workspace";
export default async function Page({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <DocumentWorkspace projectId={projectId} mode="terminology" />;
}
