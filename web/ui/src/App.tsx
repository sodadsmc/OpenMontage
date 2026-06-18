import { useCallback, useEffect, useState } from 'react'
import { API, jget } from './api'
import type { Cost, Health, Project, Scene, ScenesPayload } from './api'
import SceneDetail from './components/SceneDetail'

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

  const reload = useCallback(async (pid: string) => {
    try {
      const data = await jget<ScenesPayload>(`${API}/projects/${pid}/scenes`)
      setScenes(data.scenes); setTitle(data.title)
      setSelected((cur) => cur ?? data.scenes[0]?.id ?? null)
    } catch (e) { setError((e as Error).message) }
    try { setCost(await jget<Cost>(`${API}/projects/${pid}/cost`)) } catch { setCost(null) }
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

  return (
    <>
      <header className="topbar">
        <div className="brand">OpenMontage <span className="muted">· scene review</span></div>
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
        {scenes.length > 0 && (
          <div className="navbar">
            <button className="act" disabled={idx <= 0} onClick={() => go(-1)}>← Prev</button>
            <span className="muted">Scene {idx + 1} of {scenes.length}{selectedScene ? ` · ${selectedScene.id}` : ''} &nbsp;·&nbsp; ← / → to cycle</span>
            <button className="act" disabled={idx >= scenes.length - 1} onClick={() => go(1)}>Next →</button>
          </div>
        )}
        {project && selectedScene
          ? <SceneDetail key={selectedScene.id} project={project} scene={selectedScene} reload={() => reload(project)} />
          : <section className="detail"><p className="muted">Loading…</p></section>}

        <section>
          <div className="gridhead">All scenes <span className="muted">({scenes.length})</span></div>
          <div className="grid">
            {scenes.map((sc) => (
              <div key={sc.id} className={`card${sc.id === selected ? ' sel' : ''}`}
                   onClick={() => { setSelected(sc.id); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>
                <div className="ctop"><div className="cbadge">{sc.number}</div><span className={`dot ${statusOf(sc)}`} /></div>
                <div className="clane">{sc.lane || '—'}{sc.flf ? ' · flf' : ''}</div>
                <div className="cfoot">
                  <span>{sc.slot_s}s {sc.take_count ? <span className="takeflag">T{sc.active_take || sc.take_count}</span> : null}</span>
                  <span>{sc.auto_gate?.score != null ? <span className={scoreClass(sc.auto_gate.score)}>{sc.auto_gate.score}/10</span> : null}</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </main>
    </>
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
