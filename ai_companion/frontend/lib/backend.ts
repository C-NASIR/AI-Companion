const DEFAULT_BACKEND_URL = "http://localhost:8000";

function normalizeUrl(url: string): string {
  return url.replace(/\/+$/, "");
}

export function getBackendUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (envUrl && envUrl.trim().length > 0) {
    return normalizeUrl(envUrl.trim());
  }
  return DEFAULT_BACKEND_URL;
}

export async function streamChatRequest(
  message: string,
  runId: string,
  onChunk: (chunk: string) => void
): Promise<string> {
  const response = await fetch(`${getBackendUrl()}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X_Run_Id": runId,
    },
    body: JSON.stringify({ message }),
  });

  if (!response.body) {
    throw new Error("Streaming response body missing");
  }

  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    if (value) {
      const text = decoder.decode(value, { stream: true });
      if (text) {
        onChunk(text);
      }
    }
  }
  const leftover = decoder.decode();
  if (leftover) {
    onChunk(leftover);
  }

  return response.headers.get("X_Run_Id") ?? runId;
}
