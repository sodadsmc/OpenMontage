import { useEffect, useRef, useState } from 'react'
import { API, jget, jpost } from '../api'
import type { ClipCandidate, DraftRevision, Job, Scene, Verdict } from '../api'
import RevisionCard from './RevisionCard'

const VERDICTS: Verdict[] = ['approve', 'needs_work', 'reject']
const VLABEL: Record<Verdict, string> = { approve: 'Approve', needs_work: 'Needs work', reject: 'Reject' }
const scoreClass = (n: number) => (n >= 8 ? 'score-good' : n >= 5 ? 'score-mid' : 'score-bad')
const estUsd = (slot: number) => Math.max(0.10, 0.017 * Math.round(slot || 0)).toFixed(2)
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export default function SceneDetail({ project, scene, reload }: { project: string; scene: Scene; reload: () => Promise<void> }) {
  const base = `${API}/projects/${project}/scenes/${scene.id}`
  const [note, setNote] = useState('')
  const [sugg, setSugg] = useState('')
  const [ctype, setCtype] = useState('prompt')
  const [draft, setDraft] = useState<DraftRevision | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [viewUrl, setViewUrl] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<ClipCandidate[] | null>(null)
  const [clipFilter, setClipFilter] = useState('')
  const [swapOpen, setSwapOpen] = useState(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const fb = scene.feedback
  const g = scene.auto_gate
  const srcUrl = viewUrl || scene.clip_url
  const mode = scene.narration_mode + (scene.narration_mode_default ? ' (default)' : '')

  const capture = async (kind: string, body: unknown) => {
    try { await jpost(`${base}/${kind}`, body); await reload() } catch (e) { alert('Save failed: ' + (e as Error).message) }
  }

  const runDirectorPass = async () => {
    setBusy(true); setJob(null)
    try {
      const n = note.trim()
      if (n) await jpost(`${base}/note`, { text: n })
      await jpost(`${base}/regenerate`, { notes: n ? [n] : [], target: 'scene' })
      setDraft(await jpost<DraftRevision>(`${base}/director-pass`, {}))
      setNote('')
    } catch (e) { alert('Director pass failed: ' + (e as Error).message) }
    setBusy(false); await reload()
  }

  const pollJob = async (jobId: string) => {
    for (let i = 0; i < 180 && alive.current; i++) {
      await sleep(2000)
      let j: Job
      try { j = await jget<Job>(`${API}/projects/${project}/jobs/${jobId}`) } catch { break }
      if (!alive.current) break
      setJob(j)
      if (['succeeded', 'failed', 'blocked'].includes(j.status)) { await reload(); break }
    }
  }

  const approveAndDispatch = async (rid: string) => {
    if (!confirm(`Approve this plan and generate a new take?\nThis spends ~$${estUsd(scene.slot_s)} via grok-kie (Kie credits).`)) return
    let r: Job
    try { r = await jpost<Job>(`${base}/revision/${rid}/approve-and-dispatch`, {}) }
    catch (e) { alert('Dispatch failed: ' + (e as Error).message); return }
    setDraft(null); setJob(r); await reload()
    if (r.status === 'queued' && r.job_id) void pollJob(r.job_id)
  }

  const reject = async (rid: string) => {
    try { await jpost(`${base}/revision/${rid}/reject`, {}); setDraft(null); await reload() }
    catch (e) { alert('Failed: ' + (e as Error).message) }
  }

  const openSwap = async () => {
    const next = !swapOpen; setSwapOpen(next)
    if (next && !candidates) {
      try { setCandidates(await jget<ClipCandidate[]>(`${base}/clip-candidates`)) }
      catch (e) { alert('Load failed: ' + (e as Error).message) }
    }
  }

  const useClip = async (c: ClipCandidate) => {
    if (!confirm(`Use "${c.name}" as the clip for scene ${scene.number}?`)) return
    try { await jpost(`${base}/use-clip`, { path: c.path, label: c.name }); setViewUrl(null); setSwapOpen(false); await reload() }
    catch (e) { alert('Failed: ' + (e as Error).message) }
  }

  return (
    <section className="detail">
      <Player scene={scene} srcUrl={srcUrl} />

      {scene.take_count > 0 && (
        <div className="takesbar">
          <span className="muted">takes:</span>
          <button className={`take-chip${!viewUrl ? ' active' : ''}`} onClick={() => setViewUrl(null)}>baseline</button>
          {scene.takes.map((t) => (
            <button key={t.take} className={`take-chip ${t.verdict}${viewUrl === t.url ? ' active' : ''}`} onClick={() => setViewUrl(t.url)}>
              take {t.take} ({t.verdict}{t.score != null ? ` · ${Math.round(t.score * 100)}%` : ''})
            </button>
          ))}
        </div>
      )}

      <div className="swapwrap">
        <button className="act swapbtn" onClick={openSwap}>{swapOpen ? '▾ hide clips' : '⇄ swap to an existing clip'}</button>
        {swapOpen && (
          <div className="swappanel">
            <input placeholder="filter by name…" value={clipFilter} onChange={(e) => setClipFilter(e.target.value)} />
            {candidates === null
              ? <div className="muted" style={{ padding: '8px 0' }}>loading…</div>
              : (
                <div className="cliplist">
                  {candidates.filter((c) => c.name.toLowerCase().includes(clipFilter.toLowerCase())).slice(0, 60).map((c) => (
                    <div key={c.rel} className={`cliprow${c.seg_match ? ' seg' : ''}`}>
                      <div className="clipmeta">
                        <span className="clipname">{c.name}</span>
                        <span className="muted"> · {c.dir.replace('assets/', '')} · {c.size_mb}MB{c.seg_match ? ' · matches scene' : ''}</span>
                      </div>
                      <div className="clipactions">
                        <button className="act" onClick={() => setViewUrl(c.url)}>preview</button>
                        <button className="act use" onClick={() => useClip(c)}>use</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
          </div>
        )}
      </div>

      <p className="narr">{scene.narration}</p>

      <div className="row mb">
        <span className="chip">{scene.id}</span>
        <span className="chip">mode: {mode}</span>
        <span className="section-label" style={{ margin: 0 }}>auto-gate</span>
        {g?.verdict && <span className="chip">{g.verdict}</span>}
        {g?.score != null ? <span className={`chip ${scoreClass(g.score)}`}>{g.score}/10</span> : <span className="chip">not scored</span>}
      </div>
      {g?.missing && g.missing.length > 0 && (
        <div className="muted" style={{ fontSize: 13, margin: '-6px 0 8px' }}>missing: {g.missing.slice(0, 3).join('; ')}</div>
      )}

      <div className="verdicts">
        {VERDICTS.map((v) => (
          <button key={v} className={`vbtn ${v}${fb.verdict === v ? ' active' : ''}`} onClick={() => capture('verdict', { verdict: v })}>{VLABEL[v]}</button>
        ))}
      </div>

      <div className="section-label">Notes</div>
      {fb.notes.length > 0 && <ul className="list">{fb.notes.map((n, i) => <li key={i}>{n.text}</li>)}</ul>}
      <textarea rows={2} placeholder="Notes — what's wrong, what to keep…" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="row" style={{ marginTop: 6, justifyContent: 'flex-end' }}>
        <button className="act" onClick={() => { if (note.trim()) capture('note', { text: note.trim() }).then(() => setNote('')) }}>Add note</button>
      </div>

      <div className="section-label">Suggestion for next take</div>
      {fb.suggestions.length > 0 && <ul className="list">{fb.suggestions.map((s, i) => <li key={i}><b>{s.change_type || 'other'}:</b> {s.text}</li>)}</ul>}
      <div className="inline">
        <select value={ctype} onChange={(e) => setCtype(e.target.value)}>
          {['prompt', 'motion', 'seed', 'keyframe', 'lane', 'duration', 'other'].map((x) => <option key={x} value={x}>change: {x}</option>)}
        </select>
        <input placeholder="e.g. drive the needle harder, dim the overheads" value={sugg} onChange={(e) => setSugg(e.target.value)} />
        <button className="act" onClick={() => { if (sugg.trim()) capture('suggestion', { text: sugg.trim(), change_type: ctype }).then(() => setSugg('')) }}>Add</button>
      </div>

      <button className="regen" disabled={busy} onClick={runDirectorPass}>
        {busy ? 'Running director pass…' : `↻ Regenerate scene ${scene.number} → run director pass`}
      </button>
      <div className="hint">Your notes go to the director (rules applied), not injected raw. Approve the revised plan to generate a new take.</div>

      <div>
        {job && <JobBanner job={job} />}
        {draft && <RevisionCard scene={scene} rid={draft.revision_id} rev={draft.revision} status="drafted" onApprove={approveAndDispatch} onReject={reject} />}
        {fb.revisions.filter((r) => !draft || r.id !== draft.revision_id).slice().reverse().map((r) => (
          <RevisionCard key={r.id} scene={scene} rid={r.id} rev={r.revision} status={r.status} onApprove={approveAndDispatch} onReject={reject} />
        ))}
      </div>

      {scene.shots.length > 0 && (
        <details className="shots">
          <summary>{scene.shots.length} sub-shot{scene.shots.length > 1 ? 's' : ''} (drill-down)</summary>
          {scene.shots.map((s) => <pre key={s.shot_id}><b>{s.shot_id}</b> · {s.provider || ''}{'\n'}{s.prompt || ''}</pre>)}
        </details>
      )}
    </section>
  )
}

function Player({ scene, srcUrl }: { scene: Scene; srcUrl: string | null }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const video = videoRef.current
    if (!video || !scene.audio_url) return
    const audio = new Audio(scene.audio_url)
    audio.preload = 'metadata'
    const onPlay = () => { audio.currentTime = video.currentTime; void audio.play().catch(() => {}) }
    const onPause = () => audio.pause()
    const onSeek = () => { audio.currentTime = video.currentTime }
    const onEnded = () => audio.pause()
    const onRate = () => { audio.playbackRate = video.playbackRate }
    const onTime = () => { if (Math.abs(audio.currentTime - video.currentTime) > 0.3) audio.currentTime = video.currentTime }
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    video.addEventListener('seeking', onSeek)
    video.addEventListener('ended', onEnded)
    video.addEventListener('ratechange', onRate)
    video.addEventListener('timeupdate', onTime)
    return () => {
      audio.pause()
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
      video.removeEventListener('seeking', onSeek)
      video.removeEventListener('ended', onEnded)
      video.removeEventListener('ratechange', onRate)
      video.removeEventListener('timeupdate', onTime)
    }
  }, [scene.audio_url, srcUrl])

  return (
    <>
      <div className="player">
        <div className="badge">{scene.number}</div>
        <span className="chip lane corner-tr">{scene.lane || '—'}{scene.flf ? ' · flf' : ''}</span>
        <span className="chip corner-br">{scene.slot_s}s</span>
        {srcUrl
          ? <video ref={videoRef} src={srcUrl} controls preload="metadata" />
          : scene.audio_url
            ? <div className="noclip"><span className="nobadge">no clip for {scene.id} — narration only</span><audio src={scene.audio_url} controls /></div>
            : <span className="nobadge">no assembled clip for {scene.id}</span>}
      </div>
      {srcUrl && scene.audio_url && <div className="narrnote">▶ plays with the narration track (clips are silent until final render)</div>}
    </>
  )
}

function JobBanner({ job }: { job: Job }) {
  const map: Record<string, [string, string]> = {
    queued: ['⏳', `queued — generating a new take (~$${job.est_usd})`],
    running: ['⏳', `generating a new take (~$${job.est_usd})…`],
    succeeded: ['✓', `take ${job.take?.take ?? ''} generated${job.take?.score != null ? ` · ${Math.round(job.take.score * 100)}%` : ''}`],
    failed: ['✗', `generation failed: ${job.error || ''}`],
    blocked: ['⛔', `blocked: ${job.error || ''}`],
    not_yet_auto_dispatched: ['ℹ', job.reason || 'not auto-dispatched'],
    busy: ['⏳', 'a job is already running for this scene'],
    idempotent: ['✓', 'this revision was already generated'],
  }
  const [icon, text] = map[job.status] || ['', job.status]
  return <div className={`jobbanner ${job.status}`}>{icon} {text}</div>
}
