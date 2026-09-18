import { DocumentWorkspace } from "@/features/documents/workspace";
export default async function Page({
  params,
}: {
  params: Promise<{ projectId: string; reviewSetId: string }>;
}) {
  const { projectId, reviewSetId } = await params;
  return (
    <DocumentWorkspace
      projectId={projectId}
      reviewSetId={reviewSetId}
      mode="reviews"
    />
  );
}
