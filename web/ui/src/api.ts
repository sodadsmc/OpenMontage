// Typed client for the scene-review backend.
export const API = '/api'

export async function jget<T>(u: string): Promise<T> {
  const r = await fetch(u)
  if (!r.ok) throw new Error(`${u} -> ${r.status}`)
  return r.json() as Promise<T>
}

export async function jpost<T>(u: string, body?: unknown): Promise<T> {
  const r = await fetch(u, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!r.ok) throw new Error(`${u} -> ${r.status}`)
  return r.json() as Promise<T>
}

export async function jdel<T>(u: string): Promise<T> {
  const r = await fetch(u, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${u} -> ${r.status}`)
  return r.json() as Promise<T>
}

// ---- types ----
export type Verdict = 'approve' | 'needs_work' | 'reject'

export interface Project { id: string; title: string; scene_count: number | null }

export interface AutoGate {
  verdict?: string | null
  score?: number | null
  missing?: string[]
  suggested_prompt?: string
}

export interface Take {
  take: number
  verdict: string
  score: number | null
  cost_usd?: number
  revision_id?: string
  ts?: string
  url: string | null
  path?: string
  preview?: string
  partial?: boolean
  beats?: { idx: number; lane: string; status: string; label: string; prompt?: string; dur?: number }[]
  note?: string
  vet?: {
    sync_score?: number; polish_score?: number; overall?: string
    mismatches?: { narration?: string; on_screen?: string; issue?: string; fix?: string }[]
    suggestions?: { where?: string; issue?: string; motion_idea?: string }[]
  } | null
}

export interface DescribedAction {
  subjects?: string[]
  setting?: string
  action_sequence?: string[]
  props?: string[]
  on_screen_text?: string
  manner?: string
  characterization?: string
  depiction_mode?: string
  lane_plan?: { beat: string; lane: string }[]
}

export interface Beat { lane: string; desc?: string; prompt?: string; weight?: number; flf?: unknown }

export interface Revision {
  revised_prompt?: string
  lane?: string
  rationale?: string
  described_action?: DescribedAction
  gate_precheck?: { narration_alignment?: string; subject_named?: boolean }
  beats?: Beat[] | null
  chained?: boolean | null
  _source?: string
  _error?: string
}

export interface KeyframePreview {
  idx: number
  label: string
  path: string | null
  media: string | null
  url: string | null
  status: string
  // Advisory reference-judge verdict (gemini-2.5-pro vs the machine sheet + real photo).
  // Points the eyeball at a design mismatch; never blocks anything.
  vet?: { ok: boolean; mismatches: string[] } | null
}

// Per-beat service label + cost — mirror of web.backend.takes._beat_cost.
export const SERVICE: Record<string, string> = {
  grok: 'Grok i2v', flf_state_morph: 'Kling FLF', flf_drain: 'Kling FLF', manim: 'Manim (placeholder)',
}
export function beatCostUsd(lane: string, durS: number): number {
  if ((lane || '').startsWith('flf')) return Math.round(Math.min(durS, 15) * 0.084 * 100) / 100  // one Kling clip
  if (lane === 'grok') return Math.round(Math.ceil(durS / 6) * 0.102 * 100) / 100  // chained grok legs
  return 0  // manim/other = free placeholder
}

export interface RevisionEntry { id: string; revision: Revision; status: string; ts?: string; keyframes?: KeyframePreview[] }

export interface Feedback {
  verdict: Verdict | null
  notes: { text: string; ts?: string }[]
  suggestions: { text: string; change_type?: string; ts?: string }[]
  revisions: RevisionEntry[]
  takes: unknown[]
  event_count: number
}

export interface Shot { shot_id: string; provider?: string; prompt?: string }

export interface Scene {
  number: number
  id: string
  narration: string
  slot_s: number
  timeline_start_s: number
  lane: string | null
  flf: boolean
  narration_mode: string
  narration_mode_default: boolean
  clip_url: string | null
  audio_url: string | null
  active_take: number
  take_count: number
  takes: Take[]
  shots: Shot[]
  auto_gate: AutoGate | null
  feedback: Feedback
}

export interface ScenesPayload { project_id: string; title: string; scenes: Scene[] }
export interface Cost { total_usd: number; calls: number; by_provider: Record<string, number> }
export interface Health {
  director: { model: string; google_api_key_loaded: boolean; sdk_installed: boolean }
  dispatch: { kie_api_key: boolean; disabled: boolean; lanes: string[] }
}

export type JobStatus =
  | 'queued' | 'running' | 'succeeded' | 'failed' | 'blocked'
  | 'not_yet_auto_dispatched' | 'busy' | 'idempotent'

export interface Job {
  job_id?: string
  status: JobStatus
  est_usd?: number
  lane?: string
  error?: string
  reason?: string
  take?: Take | null
  kind?: string
  keyframes?: KeyframePreview[] | null
}

export interface DraftRevision { revision_id: string; revision: Revision }

// ---- stills-first workflow (dashboard v2) ----

export interface StageRow {
  scene_id: string
  narration_ready: boolean
  stills_approved: boolean
  animatic: string | null // media-relative path, or null if not built
  video_verdict?: string | null
}

export interface StagesPayload {
  project_id: string
  sheets_gate_open: boolean
  census_missing: boolean
  scenes: StageRow[]
}

export interface CensusEntity {
  name: string
  type?: string
  segments?: string[]
  mentions?: number
  importance?: number
  needs_sheet?: boolean
  why?: string
  sheet_path: string | null // media-relative
  reference_photos: string[] // media-relative
  verdict: 'sheet_approved' | 'sheet_rejected' | null
  verdict_ts?: string | null
  verdict_hint?: string | null
}

export interface EntitiesPayload {
  project_id?: string
  entities: CensusEntity[]
  sheets_gate_open: boolean
  census_missing: boolean
}

export interface StillNote { idx: number | null; note: string; ts?: string }

export interface StillsState {
  scene_id?: string
  stills_approved: boolean
  approved_ts?: string | null
  notes: StillNote[]
}

export interface AnimaticBuilt { media: string; path?: string }

export interface ClipCandidate {
  name: string
  dir: string
  rel: string
  path: string
  url: string
  size_mb: number
  mtime: number
  seg_match: boolean
}
