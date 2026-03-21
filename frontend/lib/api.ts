import type {
  Catalog,
  KnowledgeDocument,
  ModelConfig,
  SseEvent,
  ThreadState,
  ThreadSummary
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

export async function getCatalog(): Promise<Catalog> {
  const response = await fetch(`${API_BASE}/api/catalog`, { cache: "no-store" });
  return parseJson<Catalog>(response);
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const response = await fetch(`${API_BASE}/api/threads`, { cache: "no-store" });
  return parseJson<ThreadSummary[]>(response);
}

export async function createThread(input?: {
  title?: string;
  model_config?: ModelConfig;
  enabled_skills?: string[];
}): Promise<{ thread_id: string; title: string }> {
  const response = await fetch(`${API_BASE}/api/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input ?? {})
  });
  return parseJson<{ thread_id: string; title: string }>(response);
}

export async function getThread(threadId: string): Promise<ThreadState> {
  const response = await fetch(`${API_BASE}/api/threads/${threadId}`, { cache: "no-store" });
  return parseJson<ThreadState>(response);
}

export async function listDocuments(): Promise<KnowledgeDocument[]> {
  const response = await fetch(`${API_BASE}/api/knowledge/documents`, { cache: "no-store" });
  return parseJson<KnowledgeDocument[]>(response);
}

export async function uploadDocument(file: File): Promise<KnowledgeDocument> {
  const contentBase64 = await fileToBase64(file);
  const response = await fetch(`${API_BASE}/api/knowledge/documents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      file_name: file.name,
      content_base64: contentBase64
    })
  });
  return parseJson<KnowledgeDocument>(response);
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.split(",")[1] ?? "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export async function streamMessage(
  threadId: string,
  payload: {
    content: string;
    model_config: ModelConfig;
    enabled_skills: string[];
  },
  onEvent: (event: SseEvent) => void
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!response.ok || !response.body) {
    throw new Error(await response.text());
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSseEvent(rawEvent);
      if (parsed) onEvent(parsed);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function parseSseEvent(raw: string): SseEvent | null {
  const lines = raw.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (!eventLine || !dataLine) return null;
  return {
    event: eventLine.replace("event:", "").trim(),
    data: JSON.parse(dataLine.replace("data:", "").trim())
  };
}
