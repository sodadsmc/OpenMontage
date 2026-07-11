import { useCallback, useEffect, useState } from 'react'
import { API, jget, jpost } from './api'
import type { AnimaticBuilt, Cost, Health, Project, Scene, ScenesPayload, StageRow, StagesPayload } from './api'
import SceneDetail from './components/SceneDetail'
import SheetsPage from './components/SheetsPage'

const statusOf = (sc: Scene) => sc.feedback?.verdict || 'pending'
const scoreClass = (n: number) => (n >= 8 ? 'score-good' : n >= 5 ? 'score-mid' : 'score-bad')

export default function App() {
  const [project, setProject] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [scenes, setScenes] = useState<Scene[]>([])
  const [cost, setCost] = useState<Cost | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'scenes' | 'sheets'>('scenes')
  const [stages, setStages] = useState<StagesPayload | null>(null)
  const [animBusy, setAnimBusy] = useState(false)
  const [episodeAnim, setEpisodeAnim] = useState<string | null>(null)

  const reload = useCallback(async (pid: string) => {
    try {
      const data = await jget<ScenesPayload>(`${API}/projects/${pid}/scenes`)
      setScenes(data.scenes); setTitle(data.title)
      setSelected((cur) => cur ?? data.scenes[0]?.id ?? null)
    } catch (e) { setError((e as Error).message) }
    try { setCost(await jget<Cost>(`${API}/projects/${pid}/cost`)) } catch { setCost(null) }
    try { setStages(await jget<StagesPayload>(`${API}/projects/${pid}/stages`)) } catch { setStages(null) }
  }, [])

  useEffect(() => {
    void (async () => {
      let projects: Project[] = []
      try { projects = await jget<Project[]>(`${API}/projects`) } catch (e) { setError((e as Error).message); return }
      const pick = projects.find((p) => p.id === 'therac-25-test') || projects[0]
      if (!pick) { setError('No projects found under projects/.'); return }
      setProject(pick.id)
      try { setHealth(await jget<Health>(`${API}/health`)) } catch { setHealth(null) }
      await reload(pick.id)
    })()
  }, [reload])

  // Arrow keys cycle scenes (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      setSelected((cur) => {
        const i = scenes.findIndex((s) => s.id === cur)
        if (i < 0) return cur
        const ni = i + (e.key === 'ArrowRight' ? 1 : -1)
        return ni >= 0 && ni < scenes.length ? scenes[ni].id : cur
      })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [scenes])

  if (error) {
    return <main className="app"><p className="muted">Backend not reachable ({error}). Start it with <code>uvicorn web.backend.app:app --port 8011</code> from the repo root.</p></main>
  }

  const selectedScene = scenes.find((s) => s.id === selected) || null
  const counts: Record<string, number> = { approve: 0, needs_work: 0, reject: 0, pending: 0 }
  scenes.forEach((s) => { counts[statusOf(s)]++ })
  const idx = scenes.findIndex((s) => s.id === selected)
  const go = (d: number) => {
    const ni = idx + d
    if (ni >= 0 && ni < scenes.length) { setSelected(scenes[ni].id); window.scrollTo({ top: 0, behavior: 'smooth' }) }
  }
  const stageOf = (id: string): StageRow | null => stages?.scenes.find((r) => r.scene_id === id) ?? null

  // Full-episode animatic: $0 pacing pass — narrated stills for every scene.
  const buildEpisodeAnimatic = async () => {
    if (!project) return
    if (!confirm('Build the full-episode animatic? ($0 — every scene as narrated stills; scenes without stills get a placeholder card. Can take a few minutes.)')) return
    setAnimBusy(true)
    try {
      const r = await jpost<AnimaticBuilt>(`${API}/projects/${project}/animatic`)
      setEpisodeAnim(`${API}/projects/${project}/media/${r.media}?t=${Date.now()}`)
    } catch (e) { alert('Episode animatic failed: ' + (e as Error).message) }
    setAnimBusy(false)
  }

  return (
    <>
      <header className="topbar">
        <div className="brand">
          OpenMontage <span className="muted">· scene review</span>
          <nav className="tabs">
            <button className={`tab${tab === 'scenes' ? ' active' : ''}`} onClick={() => setTab('scenes')}>Scenes</button>
            <button className={`tab${tab === 'sheets' ? ' active' : ''}`} onClick={() => setTab('sheets')}>Sheets</button>
          </nav>
        </div>
        <div className="summary">
          <span className="chip">{title} · {scenes.length} scenes</span>
          <span className="chip"><span className="dot approve" />{counts.approve}</span>
          <span className="chip"><span className="dot needs_work" />{counts.needs_work}</span>
          <span className="chip"><span className="dot reject" />{counts.reject}</span>
          <span className="chip"><span className="dot pending" />{counts.pending}</span>
          <span className="chip">spent {cost ? `$${cost.total_usd}` : '—'}</span>
          {health && <HealthChips health={health} />}
        </div>
      </header>

      <main className="app">
        {tab === 'sheets' && project && <SheetsPage project={project} onChanged={() => reload(project)} />}

        {tab === 'scenes' && <>
        {stages && (
          <div className="gatebar">
            <span className={`chip ${stages.sheets_gate_open ? 'ok-chip' : 'warn-chip'}`}>
              Sheets gate: {stages.sheets_gate_open ? 'OPEN' : 'CLOSED'}
            </span>
            {stages.census_missing && <span className="muted" style={{ fontSize: 13 }}>no entity census yet — run it on the Sheets tab</span>}
            <span className="spacer" />
            <button className="act" disabled={animBusy} onClick={buildEpisodeAnimatic}
                    title="$0 pacing pass: every scene as narrated stills, concatenated in story order">
              {animBusy ? <><span className="spinner" />Building episode animatic…</> : '▶ Build episode animatic'}
            </button>
            {episodeAnim && <a className="chip ok-chip" href={episodeAnim} target="_blank" rel="noreferrer">episode animatic ↗</a>}
          </div>
        )}

        {scenes.length > 0 && (
          <div className="navbar">
            <button className="act" disabled={idx <= 0} onClick={() => go(-1)}>← Prev</button>
            <span className="muted">Scene {idx + 1} of {scenes.length}{selectedScene ? ` · ${selectedScene.id}` : ''} &nbsp;·&nbsp; ← / → to cycle</span>
            <button className="act" disabled={idx >= scenes.length - 1} onClick={() => go(1)}>Next →</button>
          </div>
        )}
        {project && selectedScene
          ? <SceneDetail key={selectedScene.id} project={project} scene={selectedScene}
                         stage={stageOf(selectedScene.id)} reload={() => reload(project)} />
          : <section className="detail"><p className="muted">Loading…</p></section>}

        <section>
          <div className="gridhead">All scenes <span className="muted">({scenes.length})</span></div>
          <div className="grid">
            {scenes.map((sc) => {
              const st = stageOf(sc.id)
              return (
              <div key={sc.id} className={`card${sc.id === selected ? ' sel' : ''}`}
                   onClick={() => { setSelected(sc.id); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>
                <div className="ctop"><div className="cbadge">{sc.number}</div><span className={`dot ${statusOf(sc)}`} /></div>
                <div className="clane">{sc.lane || '—'}{sc.flf ? ' · flf' : ''}</div>
                {st && <StageStrip row={st} />}
                <div className="cfoot">
                  <span>{sc.slot_s}s {sc.take_count ? <span className="takeflag">T{sc.active_take || sc.take_count}</span> : null}</span>
                  <span>{sc.auto_gate?.score != null ? <span className={scoreClass(sc.auto_gate.score)}>{sc.auto_gate.score}/10</span> : null}</span>
                </div>
              </div>
              )
            })}
          </div>
        </section>
        </>}
      </main>
    </>
  )
}

// narration / stills / video stage dots for one scene (compact strip on the board card).
function StageStrip({ row }: { row: StageRow }) {
  const stills = row.stills_approved ? 'ok' : row.animatic ? 'mid' : 'off'
  const video = row.video_verdict === 'approve' ? 'ok'
    : row.video_verdict === 'needs_work' ? 'mid'
    : row.video_verdict === 'reject' ? 'bad' : 'off'
  return (
    <div className="stagestrip" title={
      `narration: ${row.narration_ready ? 'ready' : 'missing'}\n`
      + `stills: ${row.stills_approved ? 'approved' : row.animatic ? 'animatic built, not approved' : 'none'}\n`
      + `video: ${row.video_verdict || 'no verdict'}`}>
      <span className={`sdot ${row.narration_ready ? 'ok' : 'off'}`} />
      <span className={`sdot ${stills}`} />
      <span className={`sdot ${video}`} />
    </div>
  )
}

function HealthChips({ health }: { health: Health }) {
  const dir = health.director.google_api_key_loaded && health.director.sdk_installed
  const dp = health.dispatch
  return (
    <>
      <span className={`chip${dir ? ' ok-chip' : ''}`}>director: {dir ? 'gemini' : 'offline'}</span>
      <span className={`chip${dp.kie_api_key && !dp.disabled ? ' ok-chip' : ''}`}>dispatch: {dp.disabled ? 'off' : dp.kie_api_key ? 'ready' : 'no key'}</span>
    </>
  )
}
