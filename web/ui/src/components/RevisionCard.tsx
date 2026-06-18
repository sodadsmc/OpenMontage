import type { Revision, Scene } from '../api'

const estUsd = (slot: number) => Math.max(0.10, 0.017 * Math.round(slot || 0)).toFixed(2)

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
  scene, rid, rev, status, onApprove, onReject,
}: {
  scene: Scene
  rid: string
  rev: Revision
  status: string
  onApprove: (rid: string) => void
  onReject: (rid: string) => void
}) {
  if (!rev) return null
  const da = rev.described_action || {}
  const gp = rev.gate_precheck || {}
  const src = (rev._source || '').startsWith('gemini') ? `director · ${rev._source}` : 'director · offline'
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
        <Row label="on-screen" value={da.on_screen_text} />
        <Row label="manner" value={da.manner} />
        <Row label="characterization" value={da.characterization} />
        {da.lane_plan && da.lane_plan.length > 0 && (
          <div className="da-row">
            <span>lanes</span>
            <div>{da.lane_plan.map((l, i) => <div key={i}>{l.beat} → <b>{l.lane}</b></div>)}</div>
          </div>
        )}
      </div>

      <div className="section-label">revised prompt</div>
      <pre className="revprompt">{rev.revised_prompt}</pre>
      <div className="section-label">why</div>
      <div className="muted" style={{ fontSize: 13 }}>{rev.rationale}</div>

      <div className="row" style={{ marginTop: 8 }}>
        <span className="chip">pre-gate: {gp.narration_alignment || '—'}</span>
        <span className="chip">subject named: {gp.subject_named ? 'yes' : 'no'}</span>
        <span className="chip muted">{src}</span>
      </div>
      {rev._error && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>degraded: {rev._error}</div>}

      {status === 'drafted' && (
        <div className="row" style={{ marginTop: 10, gap: 10 }}>
          <button className="vbtn approve" onClick={() => onApprove(rid)}>Approve &amp; generate (~${estUsd(scene.slot_s)})</button>
          <button className="vbtn reject" onClick={() => onReject(rid)}>Reject</button>
        </div>
      )}
    </div>
  )
}
