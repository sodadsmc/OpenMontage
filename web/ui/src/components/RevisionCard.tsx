import { useState } from 'react'
import type { Beat, Revision, Scene } from '../api'
import { SERVICE, beatCostUsd } from '../api'

const LANES = ['grok', 'flf_state_morph', 'flf_drain', 'manim']

function Row({ label, value }: { label: string; value?: string | string[] | null }) {
  if (!value || (Array.isArray(value) && value.length === 0)) return null
  return (
    <div className="da-row">
      <span>{label}</span>
      <div>{Array.isArray(value) ? value.join(', ') : value}</div>
    </div>
  )
}

export default function RevisionCard({
  scene, rid, rev, status, onApprove, onReject, onApproveEdited,
}: {
  scene: Scene
  rid: string
  rev: Revision
  status: string
  onApprove: (rid: string) => void
  onReject: (rid: string) => void
  onApproveEdited?: (rid: string, beats: Beat[], total: number) => void
}) {
  const isMixed = rev?.lane === 'mixed' && Array.isArray(rev?.beats) && (rev?.beats?.length ?? 0) > 0
  const [beats, setBeats] = useState<Beat[]>(() => (rev?.beats || []).map((b) => ({ ...b })))
  const [edited, setEdited] = useState(false)
  if (!rev) return null

  const da = rev.described_action || {}
  const gp = rev.gate_precheck || {}
  const slot = scene.slot_s || 0
  const totW = beats.reduce((s, b) => s + (Number(b.weight) || 0), 0) || 1
  const beatDur = (b: Beat) => (slot * (Number(b.weight) || 0)) / totW
  const beatCost = (b: Beat) => beatCostUsd(b.lane, beatDur(b))
  const total = isMixed
    ? Math.round(beats.reduce((s, b) => s + beatCost(b), 0) * 100) / 100
    : beatCostUsd(rev.lane || 'grok', slot)
  const src = (rev._source || '').startsWith('gemini') ? `director · ${rev._source}` : 'director · offline'

  const update = (i: number, patch: Partial<Beat>) => {
    setBeats((bs) => bs.map((b, j) => (j === i ? { ...b, ...patch } : b)))
    setEdited(true)
  }
  const approve = () => {
    const what = isMixed ? `${beats.length}-beat plan` : `${rev.lane || 'grok'} shot`
    if (!confirm(`Generate this ${what}? (~$${total.toFixed(2)} via the per-beat services)`)) return
    if (isMixed && edited && onApproveEdited) onApproveEdited(rid, beats, total)
    else onApprove(rid)
  }

  const statusChip = status === 'drafted'
    ? <span className="rev-status drafted">awaiting approval</span>
    : status === 'approved'
      ? <span className="rev-status approved">approved</span>
      : status === 'rejected'
        ? <span className="rev-status rejected">rejected</span>
        : null

  return (
    <div className={`revcard ${status}`}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <div><b>Director's revised plan</b> <span className="chip">{rev.lane || ''}</span> <span className="chip">{da.depiction_mode || ''}</span></div>
        {statusChip}
      </div>

      <div className="da">
        <Row label="subjects" value={da.subjects} />
        <Row label="setting" value={da.setting} />
        {da.action_sequence && da.action_sequence.length > 0 && (
          <div className="da-row">
            <span>action</span>
            <ol className="da-seq">{da.action_sequence.map((a, i) => <li key={i}>{a}</li>)}</ol>
          </div>
        )}
        <Row label="props" value={da.props} />
        <Row label="manner" value={da.manner} />
      </div>

      {isMixed ? (
        <>
          <div className="section-label">beats — service &amp; cost (editable)</div>
          <div className="beat-editor">
            {beats.map((b, i) => (
              <div key={i} className="beat-edit">
                <div className="beat-edit-top">
                  <span className="beat-num">{i + 1}</span>
                  <select value={b.lane} onChange={(e) => update(i, { lane: e.target.value })}>
                    {LANES.map((l) => <option key={l} value={l}>{SERVICE[l] || l}</option>)}
                  </select>
                  <input type="number" step="0.05" min="0.05" value={b.weight ?? 0}
                    onChange={(e) => update(i, { weight: Number(e.target.value) })} title="duration share" />
                  <span className="beat-cost">~{beatDur(b).toFixed(1)}s · ${beatCost(b).toFixed(2)}</span>
                  {b.desc && <span className="muted beat-desc">{b.desc}</span>}
                </div>
                <textarea rows={2} value={b.prompt || ''} placeholder="prompt for this beat"
                  onChange={(e) => update(i, { prompt: e.target.value })} />
              </div>
            ))}
            <div className="beat-total">total ~${total.toFixed(2)} for {slot.toFixed(0)}s {edited && <span className="chip">edited</span>}</div>
          </div>
        </>
      ) : (
        <>
          <div className="section-label">revised prompt</div>
          <pre className="revprompt">{rev.revised_prompt}</pre>
        </>
      )}

      <div className="section-label">why</div>
      <div className="muted" style={{ fontSize: 13 }}>{rev.rationale}</div>

      <div className="row" style={{ marginTop: 8 }}>
        <span className="chip">pre-gate: {gp.narration_alignment || '—'}</span>
        <span className="chip muted">{src}</span>
      </div>
      {rev._error && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>degraded: {rev._error}</div>}

      {status === 'drafted' && (
        <div className="row" style={{ marginTop: 10, gap: 10 }}>
          <button className="vbtn approve" onClick={approve}>
            {isMixed && edited ? `Save & generate (~$${total.toFixed(2)})` : `Approve & generate (~$${total.toFixed(2)})`}
          </button>
          <button className="vbtn reject" onClick={() => onReject(rid)}>Reject</button>
        </div>
      )}
    </div>
  )
}
