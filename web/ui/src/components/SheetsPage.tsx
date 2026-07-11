import { useCallback, useEffect, useState } from 'react'
import { API, jget, jpost } from '../api'
import type { CensusEntity, EntitiesPayload } from '../api'

// Entity reference-sheet review: the project-wide gate before any stills/video
// spend. Census entities are listed needs_sheet first (then by importance);
// each sheet is vetted against the REAL reference photos next to it.
export default function SheetsPage({ project, onChanged }: { project: string; onChanged?: () => Promise<void> }) {
  const base = `${API}/projects/${project}`
  const [data, setData] = useState<EntitiesPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [censusRunning, setCensusRunning] = useState(false)
  const [busyName, setBusyName] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setData(await jget<EntitiesPayload>(`${base}/entities`)); setError(null) }
    catch (e) { setError((e as Error).message) }
  }, [base])
  useEffect(() => { void load() }, [load])

  const runCensus = async () => {
    if (!confirm('Run the entity census over the scored script? (LLM extraction, ~30s)')) return
    setCensusRunning(true)
    try { setData(await jpost<EntitiesPayload>(`${base}/entities/census`)); await onChanged?.() }
    catch (e) { alert('Census failed: ' + (e as Error).message) }
    setCensusRunning(false)
  }

  const sendVerdict = async (ent: CensusEntity, verdict: 'approve' | 'reject') => {
    let hint = ''
    if (verdict === 'reject') {
      const h = window.prompt(`Reject the sheet for "${ent.name}" — what's wrong / what to fix?`)
      if (h === null) return
      hint = h.trim()
    }
    setBusyName(ent.name)
    try {
      // sheet_path binds the verdict to the exact file being vetted (beats fuzzy matching).
      await jpost(`${base}/entities/${encodeURIComponent(ent.name)}/verdict`,
        { verdict, hint, sheet_path: ent.sheet_path || '' })
      await load(); await onChanged?.()
    } catch (e) { alert('Verdict failed: ' + (e as Error).message) }
    setBusyName(null)
  }

  if (error) return <section className="detail"><p className="muted">Sheets load failed ({error}).</p></section>
  if (!data) return <section className="detail"><p className="muted">Loading entities…</p></section>

  // needs_sheet first, then by importance (desc)
  const entities = data.entities.slice().sort((a, b) =>
    Number(!!b.needs_sheet) - Number(!!a.needs_sheet) || (b.importance || 0) - (a.importance || 0))
  // Media-relative posix path -> URL. The backend sometimes falls back to a repo-relative
  // Windows path (projects\pid\assets\...) — normalize for DISPLAY only; the verdict body
  // still sends the ORIGINAL sheet_path so the explicit binding matches what the API returned.
  const media = (p: string) => {
    const norm = p.replace(/\\/g, '/').replace(`projects/${project}/`, '')
    return `${base}/media/${encodeURI(norm)}`
  }

  return (
    <section className="sheets">
      <div className="gatebar">
        <span className={`chip ${data.sheets_gate_open ? 'ok-chip' : 'warn-chip'}`}>
          Sheets gate: {data.sheets_gate_open ? 'OPEN' : 'CLOSED'}
        </span>
        <span className="muted" style={{ fontSize: 13 }}>
          {data.census_missing
            ? 'no entity census yet — run it to find what needs a reference sheet'
            : `${entities.filter((e) => e.needs_sheet).length} of ${entities.length} entities need a sheet · every needs-sheet entity must be approved to open the gate`}
        </span>
        <span className="spacer" />
        {data.census_missing && (
          <button className="act" disabled={censusRunning} onClick={runCensus}>
            {censusRunning ? <><span className="spinner" />Running census…</> : '▶ Run census'}
          </button>
        )}
      </div>

      {entities.length === 0 && !data.census_missing && <p className="muted">Census ran but found no entities.</p>}

      {entities.map((ent) => (
        <div key={ent.name} className={`sheetrow${ent.verdict === 'sheet_approved' ? ' approved' : ent.verdict === 'sheet_rejected' ? ' rejected' : ''}`}>
          <div className="sheetmeta">
            <div className="row">
              <b>{ent.name}</b>
              <span className="chip">{ent.type || '?'}</span>
              <span className="chip">imp {ent.importance ?? '—'}</span>
              {ent.needs_sheet ? <span className="chip lane">needs sheet</span> : <span className="chip">no sheet needed</span>}
              {ent.verdict === 'sheet_approved' && <span className="rev-status approved">✓ approved</span>}
              {ent.verdict === 'sheet_rejected' && <span className="rev-status rejected">✗ rejected</span>}
            </div>
            {ent.why && <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>{ent.why}</div>}
            {ent.segments && ent.segments.length > 0 && (
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                {ent.segments.length} segment{ent.segments.length > 1 ? 's' : ''}: {ent.segments.slice(0, 8).join(', ')}{ent.segments.length > 8 ? '…' : ''}
              </div>
            )}
            {ent.verdict === 'sheet_rejected' && ent.verdict_hint && (
              <div style={{ fontSize: 12, color: 'var(--bad)', marginTop: 4 }}>hint: {ent.verdict_hint}</div>
            )}
            <div className="row" style={{ marginTop: 10, gap: 10 }}>
              <button className="vbtn approve" disabled={busyName === ent.name || !ent.sheet_path}
                      title={ent.sheet_path ? 'approve this sheet vs the real photos' : 'no sheet file found to approve'}
                      onClick={() => sendVerdict(ent, 'approve')}>✓ Approve</button>
              <button className="vbtn reject" disabled={busyName === ent.name}
                      onClick={() => sendVerdict(ent, 'reject')}>✗ Reject</button>
            </div>
          </div>
          <div className="sheetmedia">
            {ent.sheet_path
              ? <a href={media(ent.sheet_path)} target="_blank" rel="noreferrer">
                  <img className="sheetimg" src={media(ent.sheet_path)} alt={`${ent.name} reference sheet`} loading="lazy" />
                </a>
              : <div className="nosheet muted">no sheet file found{ent.needs_sheet ? ' — build one (lib/entity_census → sheet)' : ''}</div>}
            {ent.reference_photos.length > 0 && (
              <div className="refstrip">
                {ent.reference_photos.map((p) => (
                  <a key={p} href={media(p)} target="_blank" rel="noreferrer" title={p}>
                    <img className="refthumb" src={media(p)} alt={`${ent.name} real reference`} loading="lazy" />
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </section>
  )
}
