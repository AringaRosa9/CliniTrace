export async function result<T>(
  promise: Promise<{ data?: T; response: Response }>,
): Promise<T> {
  let value;
  try {
    value = await promise;
  } catch {
    throw new Error("连接中断，请保留当前输入并重试。");
  }
  if (!value.response.ok) {
    const status = value.response.status;
    if (status === 401) window.dispatchEvent(new Event("session-expired"));
    throw new Error(
      (
        {
          401: "会话已过期，请重新登录。",
          403: "没有此项操作权限。",
          404: "记录不存在或不在授权范围内。",
          409: "版本已变化。请刷新并对照最新值；当前输入已保留，请勿直接覆盖他人修改。",
          410: "导出文件已过期，请重新创建导出。",
          422: "未满足审核条件，或字段值与原文证据不符。请核对缺失语义、证据及待处理问题。",
        } as Record<number, string>
      )[status] ?? "服务暂不可用，请稍后重试。",
    );
  }
  return value.data as T;
}
