export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Learning-Client": "workbench",
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json();
}

export async function streamChat(
  body: object,
  signal: AbortSignal,
  onEvent: (e: any) => void,
) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Learning-Client": "workbench",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const e = await res.json();
    throw new Error(typeof e.detail === "string" ? e.detail : "请求失败");
  }
  if (!res.body) throw new Error("当前浏览器不支持流式响应");
  const reader = res.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts)
        if (part.startsWith("data: ")) onEvent(JSON.parse(part.slice(6)));
    }
  } finally {
    reader.releaseLock();
  }
}
