export interface User {
  id: string
  email: string
  display_name: string
  role: 'admin' | 'member'
  is_active: boolean
  created_at: string
}

export interface Repository {
  id: string
  name: string
  description: string
  git_url: string
  branch: string
  visibility: 'public' | 'private'
  license_name: string
  license_url: string
  status: string
  chunk_count: number
  last_commit: string
  last_indexed_at: string | null
}

export interface GitLabSource {
  id: string
  name: string
  base_url: string
  group_path: string
  credential_ref: string
  enabled: boolean
  poll_interval_seconds: number
  last_checked_at: string | null
  last_error: string
  created_at: string
}

export interface GitLabProject {
  external_id: string
  path_with_namespace: string
  name: string
  description: string
  default_branch: string
  web_url: string
  git_url: string
}

export interface GitHubSource {
  id: string
  name: string
  repo_url: string
  owner: string
  repository: string
  branch: string
  repository_id: string
  repository_status: string
  enabled: boolean
  poll_interval_seconds: number
  last_checked_at: string | null
  last_error: string
  created_at: string
  deploy_key_configured: boolean
}

export interface IndexJob {
  id: string
  repository_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  progress: number
  message: string
  error: string
  commit: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface ApiToken {
  id: string
  name: string
  prefix: string
  scopes: string[]
  repository_ids: string[]
  created_at: string
  expires_at: string | null
  revoked_at: string | null
  token?: string
}

export interface SearchResult {
  repo: string
  generation_id: string
  commit: string
  path: string
  language: string
  symbol: string
  start_line: number
  end_line: number
  score: number
  vector_score: number
  lexical_score: number
  retrieval: 'hybrid' | 'vector' | 'lexical'
  snippet: string
}

export interface FilePreview {
  repo: string
  commit: string
  path: string
  start_line: number
  end_line: number
  content: string
}

export interface ChatCitation {
  repo: string
  path: string
  symbol: string
  start_line: number
  end_line: number
}

export interface ChatResponse {
  answer: string
  citations: ChatCitation[]
}

export interface ChatStatus {
  enabled: boolean
  model: string
  provider: string
}

export interface LlmModel {
  id: string
  name: string
}

export interface LlmProvider {
  id: string
  name: string
  base_url: string
  model: string
  models: LlmModel[]
  is_active: boolean
  api_key_configured: boolean
  last_synced_at: string | null
}

export interface TreeEntry {
  name: string
  path: string
  type: 'dir' | 'file'
  size: number | null
}

export interface TreeResponse {
  path: string
  entries: TreeEntry[]
}

export interface Stats {
  repository_count: number
  ready_count: number
  chunk_total: number
  languages: { language: string; chunks: number }[]
}
