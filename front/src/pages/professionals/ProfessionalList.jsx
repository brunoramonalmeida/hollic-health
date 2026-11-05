import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { WEEKDAYS_SHORT } from '../../dates'
import { capacityLabel } from '../../labels'
import { useSession } from '../../session'

/** "Mon 08:00-12:00, 13:00-17:00 · Tue …" compacted to one line. */
export function describeWeek(hours) {
  if (!hours.length) return 'No working hours'
  const byDay = new Map()
  hours.forEach((item) => {
    if (!byDay.has(item.weekday)) byDay.set(item.weekday, [])
    byDay.get(item.weekday).push(`${item.start}-${item.end}`)
  })
  return [...byDay.entries()]
    .sort(([a], [b]) => a - b)
    .map(([weekday, ranges]) => `${WEEKDAYS_SHORT[weekday]} ${ranges.join(', ')}`)
    .join(' · ')
}

export default function ProfessionalList() {
  const { canEditPricing } = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/professionals'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete "${row.name}"?`)) return
    setError('')
    try {
      await api(`/professionals/${row.id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Records / Professionals"
        title="Professionals"
        hint="Weekly working hours define the slots offered for appointments, up to the service capacity reserved for the network; on-call shifts are priced at the on-call rates. Professionals may also offer remote appointments (video call) in the same hours. The agenda shows what is taken, off or full."
      >
        <span className="count">{rows.length} records</span>
        {canEditPricing && <Link className="button" to="/professionals/new">New professional</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Specialty</th>
              <th>Clinic</th>
              <th>Fee</th>
              <th>On-call</th>
              <th>Urgent</th>
              <th>Remote</th>
              <th>Slot</th>
              <th>Capacity</th>
              <th>Working hours</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.specialty_name}</td>
                <td>{row.clinic_name}</td>
                <td>{money(row.price_cents)}</td>
                <td>
                  {row.on_call_enabled ? (
                    <>
                      <span className="badge on-call">{money(row.on_call_price_cents)}</span>
                      <br />
                      <small className="muted">{row.on_call_hours.length ? describeWeek(row.on_call_hours) : 'No shifts yet'}</small>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>{row.urgent_enabled ? <span className="badge urgent">{money(row.urgent_price_cents)}</span> : <span className="muted">—</span>}</td>
                <td>{row.remote_enabled ? <span className="badge remote">{money(row.remote_fee_cents)}</span> : <span className="muted">—</span>}</td>
                <td>{row.slot_minutes} min</td>
                <td>
                  {capacityLabel(row.daily_limit, row.monthly_limit)}
                  {(row.clinic_daily_limit > 0 || row.clinic_monthly_limit > 0) && (
                    <small className="muted"> · clinic {capacityLabel(row.clinic_daily_limit, row.clinic_monthly_limit)}</small>
                  )}
                </td>
                <td className="week-summary">{describeWeek(row.working_hours)}</td>
                <td className="actions">
                  <Link className="button" to={`/professionals/${row.id}/agenda`}>Agenda</Link>
                  <Link className="button ghost" to={`/professionals/${row.id}`}>{canEditPricing ? 'Edit' : 'View'}</Link>
                  {canEditPricing && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={11} className="muted">No professionals yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
