const decoder = new TextDecoder();

function processBuffer(
  buffer: string,
  onJson: (value: unknown) => void
): string {
  let newlineIndex = buffer.indexOf("\n");
  while (newlineIndex !== -1) {
    const line = buffer.slice(0, newlineIndex);
    buffer = buffer.slice(newlineIndex + 1);
    const trimmed = line.trim();
    if (trimmed.length > 0) {
      try {
        onJson(JSON.parse(trimmed));
      } catch (error) {
        console.error("Failed to parse NDJSON line", trimmed, error);
      }
    }
    newlineIndex = buffer.indexOf("\n");
  }
  return buffer;
}

export async function parseNdjsonStream(
  stream: ReadableStream<Uint8Array>,
  onJson: (value: unknown) => void
): Promise<void> {
  const reader = stream.getReader();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = processBuffer(buffer, onJson);
  }

  buffer += decoder.decode();
  const trimmed = buffer.trim();
  if (trimmed.length > 0) {
    try {
      onJson(JSON.parse(trimmed));
    } catch (error) {
      console.error("Failed to parse trailing NDJSON payload", trimmed, error);
    }
  }
}
