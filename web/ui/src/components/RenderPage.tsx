import { useCallback, useEffect, useRef, useState } from 'react'
import { API, jget, jpost } from '../api'
import type { QcPayload, QcReport, RenderJob, RenderStart, RenderStatus } from '../api'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
const STAGES = ['build', 'render', 'qc'] as const

// Render & QC tab: the final mile in the cockpit — kick the full
// build -> render -> machine-QC job, watch its stage, then review the QC
// findings against the fresh render (click a finding to seek the player).
export default function RenderPage({ project }: { project: string }) {
  const base = `${API}/projects/${project}`
  const [job, setJob] = useState<RenderJob | null>(null)
  const [report, setReport] = useState<QcReport | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const alive = useRef(true)
  const polling = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const loadQc = useCallback(async () => {
    try { setReport((await jget<QcPayload>(`${base}/qc`)).report) } catch { setReport(null) }
    setLoaded(true)
  }, [base])

  const loadStatus = useCallback(async (): Promise<RenderJob | null> => {
    try {
      const j = (await jget<RenderStatus>(`${base}/render/status`)).job
      if (alive.current) setJob(j)
      return j
    } catch { return null }
  }, [base])

  // Poll every 5s while the job is queued/running; refresh the QC report when it lands.
  const poll = useCallback(async () => {
    if (polling.current) return
    polling.current = true
    try {
      for (;;) {
        await sleep(5000)
        if (!alive.current) return
        const j = await loadStatus()
        if (!j || !['queued', 'running'].includes(j.status)) break
      }
      if (alive.current) await loadQc()
    } finally { polling.current = false }
  }, [loadStatus, loadQc])

  useEffect(() => {
    void loadQc()
    void loadStatus().then((j) => {
      if (j && ['queued', 'running'].includes(j.status)) void poll()
    })
  }, [loadQc, loadStatus, poll])

  const startRender = async () => {
    if (!confirm('Run the full build → render → machine-QC for this project?\nTakes ~20+ minutes (background job on the server); the QC report shows up here when it lands.')) return
    setBusy(true)
    try {
      const r = await jpost<RenderStart>(`${base}/render`)
      if (r.status === 'blocked') alert('Render blocked: ' + (r.error || 'unknown'))
      await loadStatus()
      if (r.status === 'queued' || r.status === 'busy') void poll()
    } catch (e) { alert('Render failed to start: ' + (e as Error).message) }
    setBusy(false)
  }

  const seek = (at: number) => {
    const v = videoRef.current
    if (!v) return
    v.currentTime = Math.max(0, at)
    void v.play().catch(() => {})
  }

  const working = !!job && ['queued', 'running'].includes(job.status)

  return (
    <section className="renderpage">
      <div className="gatebar">
        <button className="act" disabled={busy || working} onClick={startRender}
                title="full build → render → machine QC as one background job (~25 min)">
          {working ? <><span className="spinner" />Render job running…</> : '▶ Build + render + QC'}
        </button>
        {working && job && (
          <span className="stagechips">
            {STAGES.map((s) => (
              <span key={s} className={`chip${job.stage === s ? ' lane' : ''}`}>{s}</span>
            ))}
          </span>
        )}
        <span className="spacer" />
        {job && <span className="muted" style={{ fontSize: 12 }}>
          job {job.job_id} · started {new Date(job.created_ts * 1000).toLocaleTimeString()}
        </span>}
      </div>

      {job && <RenderJobBanner job={job} />}

      <section className="detail">
        <div className="section-label" style={{ marginTop: 0 }}>Machine QC report</div>
        {!report ? (
          <p className="muted">{loaded ? 'no QC report yet — run a build + render to produce one' : 'loading…'}</p>
        ) : (
          <>
            <div className="row mb">
              <span className="chip">render {mmss(report.duration_s)}</span>
              <span className={`chip ${report.findings.length ? 'warn-chip' : 'ok-chip'}`}>
                {report.findings.length} finding{report.findings.length === 1 ? '' : 's'}
              </span>
              {report.render_media && <span className="chip">{report.render_media}</span>}
            </div>
            {report.render_media
              ? <video ref={videoRef} className="rendervid" controls preload="metadata"
                       src={`${API}/projects/${project}/media/${encodeURI(report.render_media)}`} />
              : <p className="muted">render file not found under the project — findings listed below can't seek</p>}
            {report.findings.length === 0
              ? <p className="muted" style={{ marginBottom: 0 }}>clean — no findings</p>
              : (
                <>
                  <div className="hint" style={{ textAlign: 'left' }}>click a finding to seek the render to that moment</div>
                  <table className="qctable">
                    <tbody>
                      {report.findings.map((f, i) => (
                        <tr key={i} className="qcrow" onClick={() => seek(f.at)}>
                          <td><span className="chip">{f.check}</span></td>
                          <td className="qcat">{mmss(f.at)}</td>
                          <td className="muted">{f.scene || ''}</td>
                          <td>{f.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
          </>
        )}
      </section>
    </section>
  )
}

function RenderJobBanner({ job }: { job: RenderJob }) {
  const map: Record<string, [string, string]> = {
    queued: ['⏳', 'queued — full build → render → QC (~25 min)'],
    running: ['⏳', `running — stage: ${job.stage || '…'}`],
    done: ['✓', `render + QC done${job.qc_findings != null ? ` · ${job.qc_findings} finding${job.qc_findings === 1 ? '' : 's'}` : ''}`],
    failed: ['✗', `render job failed: ${job.error || ''}`],
  }
  const [icon, text] = map[job.status] || ['', job.status]
  const spinning = ['queued', 'running'].includes(job.status)
  return <div className={`jobbanner ${job.status}`}>{spinning ? <span className="spinner" /> : `${icon} `}{text}</div>
}
