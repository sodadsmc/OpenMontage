import { useState } from 'react'
import type { Scene, Take } from '../api'
import { SERVICE, beatCostUsd } from '../api'

const LANES = ['grok', 'flf_state_morph', 'flf_drain', 'manim']
type Edit = { redo: boolean; lane: string; prompt: string }

// Redo only the beats you select (reuse the rest). For each picked beat you can change its
// service (lane) and rewrite its prompt; cost counts only the picked dispatchable beats.
export default function BeatFixer({ scene, take, working, onRegen }: {
  scene: Scene
  take: Take
  working: boolean
  onRegen: (srcTake: number, edits: { idx: number; lane: string; prompt: string }[], total: number) => void
}) {
  const beats = take.beats || []
  const slot = scene.slot_s || 0
  const [open, setOpen] = useState(false)
  const [edits, setEdits] = useState<Record<number, Edit>>(() =>
    Object.fromEntries(beats.map((b) => [b.idx, { redo: b.status !== 'generated', lane: b.lane, prompt: b.prompt || b.label || '' }])),
  )
  if (beats.length === 0) return null

  const dur = (b: typeof beats[number]) => b.dur || slot / Math.max(1, beats.length)
  const set = (idx: number, patch: Partial<Edit>) => setEdits((e) => ({ ...e, [idx]: { ...e[idx], ...patch } }))
  const selected = beats.filter((b) => edits[b.idx]?.redo)
  const total = Math.round(selected.reduce((s, b) => s + beatCostUsd(edits[b.idx].lane, dur(b)), 0) * 100) / 100

  const go = () => {
    const payload = selected.map((b) => ({ idx: b.idx, lane: edits[b.idx].lane, prompt: edits[b.idx].prompt }))
    if (!payload.length) { alert('Check at least one beat to redo.'); return }
    onRegen(take.take, payload, total)
  }

  return (
    <div className="beatfix">
      <button className="act" onClick={() => setOpen((o) => !o)}>
        {open ? '▾ hide beat fixer' : `✎ fix beats of take ${take.take}`}
      </button>
      {open && (
        <div className="beatfix-panel">
          {beats.map((b) => {
            const e = edits[b.idx]
            return (
              <div key={b.idx} className={`beat-edit${e?.redo ? ' redo' : ''}`}>
                <div className="beat-edit-top">
                  <label className="beat-redo">
                    <input type="checkbox" checked={!!e?.redo} onChange={(ev) => set(b.idx, { redo: ev.target.checked })} />
                    {b.idx}. redo
                  </label>
                  <select value={e?.lane} disabled={!e?.redo} onChange={(ev) => set(b.idx, { lane: ev.target.value })}>
                    {LANES.map((l) => <option key={l} value={l}>{SERVICE[l] || l}</option>)}
                  </select>
                  <span className="beat-cost">{e?.redo ? `~$${beatCostUsd(e.lane, dur(b)).toFixed(2)}` : `reuse · ${b.status}`}</span>
                  {b.label && <span className="muted beat-desc">{b.label}</span>}
                </div>
                {e?.redo && (
                  <textarea rows={2} value={e.prompt} placeholder="new prompt for this beat (be concrete for grok)"
                    onChange={(ev) => set(b.idx, { prompt: ev.target.value })} />
                )}
              </div>
            )
          })}
          <div className="row" style={{ justifyContent: 'space-between', marginTop: 4 }}>
            <span className="beat-total">redo {selected.length} · keep {beats.length - selected.length} · ~${total.toFixed(2)}</span>
            <button className="vbtn approve" disabled={working || !selected.length} onClick={go}>
              Regenerate selected (~${total.toFixed(2)})
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
