export type ModelConfig = {
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
};

export type Citation = {
  document_id: string;
  document_name: string;
  chunk_id: string;
  snippet: string;
};

export type ToolEvent = {
  id: string;
  tool_name: string;
  status: "started" | "completed" | "failed";
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  started_at: string;
  ended_at?: string | null;
  note?: string | null;
};

export type ChatMessage = {
  id?: string;
  role: "human" | "assistant" | "system";
  content: string;
  created_at?: string;
};

export type FinalResponse = {
  answer: string;
  citations: Citation[];
  used_tools: string[];
  next_actions: string[];
};

export type SkillDescriptor = {
  id: string;
  name: string;
  description: string;
  category: "core" | "tool" | "knowledge";
  tools: string[];
  enabled_by_default: boolean;
  requires_rag: boolean;
  learning_focus: string[];
};

export type ThreadSummary = {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
};

export type ThreadState = {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  model_config: ModelConfig;
  enabled_skills: string[];
  messages: ChatMessage[];
  tool_events: ToolEvent[];
  final_output: FinalResponse | null;
};

export type Catalog = {
  models: ModelConfig[];
  skills: SkillDescriptor[];
  tools: {
    name: string;
    description: string;
    skill_id: string;
    category: string;
    learning_focus: string[];
  }[];
  learning_focus: { name: string; description: string }[];
};

export type KnowledgeDocument = {
  id: string;
  name: string;
  type: string;
  status: "processing" | "ready" | "error";
  chunk_count: number;
  created_at: string;
  error_message?: string | null;
};

export type SseEvent = {
  event: string;
  data: Record<string, unknown>;
};
