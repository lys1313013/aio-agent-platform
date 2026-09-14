/** 宠物状态使用短请求，给记忆列表、聊天等请求保留同源连接。 */
export function pollTaskSnapshots(
  load: (signal: AbortSignal) => Promise<unknown[]>,
  receive: (event: Record<string, unknown>) => void,
) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  const poll = async () => {
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 10_000);
    try {
      const tasks = await load(controller.signal);
      if (!stopped) receive({ type: 'pet_task_snapshot', tasks });
    } catch {
      // 短暂网络故障保留上次状态，下个周期重试。
    } finally {
      clearTimeout(timeout);
      if (!stopped) timer = setTimeout(poll, 5_000);
    }
  };
  void poll();
  return () => {
    stopped = true;
    clearTimeout(timer);
    controller?.abort();
  };
}
