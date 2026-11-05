import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import WeekCalendar from '../../components/WeekCalendar'
import { addDays, formatDate, isoDate, startOfWeek, today } from '../../dates'
import { useSession } from '../../session'
import { describeWeek } from './ProfessionalList'
import { capacityLabel } from '../../labels'

const emptyOff = { date: '', wholeDay: true, start: '', end: '', reason: '' }

function addMinutes(time, minutes) {
  const [hours, mins] = time.split(':').map(Number)
  const total = hours * 60 + mins + minutes
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

/** Availability of one professional: week calendar plus time off management. */
export default function ProfessionalAgenda() {
  const { id } = useParams()
  const { canManageTimeOff, canEditPricing } = useSession()
  const [professional, setProfessional] = useState(null)
  const [clinic, setClinic] = useState(null)
  const [weekStart, setWeekStart] = useState(() => startOfWeek(today()))
  const [timeOff, setTimeOff] = useState([])
  const [off, setOff] = useState(emptyOff)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    api(`/professionals/${id}`)
      .then((row) => {
        setProfessional(row)
        return api(`/clinics/${row.clinic_id}`).then(setClinic)
      })
      .catch((err) => setError(err.message))
  }, [id])

  async function loadTimeOff() {
    const start = isoDate(weekStart)
    const end = isoDate(addDays(weekStart, 6))
    setTimeOff(await api(`/professionals/${id}/time-off?start=${start}&end=${end}`))
  }

  useEffect(() => {
    loadTimeOff().catch((err) => setError(err.message))
  }, [id, weekStart.getTime(), refresh])

  function setField(field, value) {
    setOff((current) => ({ ...current, [field]: value }))
  }

  async function createTimeOff(body) {
    setError('')
    try {
      await api(`/professionals/${id}/time-off`, { method: 'POST', body, message: 'Time off registered' })
      setOff(emptyOff)
      setRefresh((n) => n + 1)
    } catch (err) {
      setError(err.message)
    }
  }

  function submitTimeOff(event) {
    event.preventDefault()
    createTimeOff({
      date: off.date,
      start: off.wholeDay ? null : off.start,
      end: off.wholeDay ? null : off.end,
      reason: off.reason,
    })
  }

  function blockSlot(date, time) {
    if (!professional) return
    const end = addMinutes(time, professional.slot_minutes)
    if (!window.confirm(`Mark ${formatDate(date)} ${time}–${end} as time off?`)) return
    createTimeOff({ date, start: time, end, reason: '' })
  }

  async function removeTimeOff(item) {
    setError('')
    try {
      await api(`/professionals/${id}/time-off/${item.id}`, { method: 'DELETE', message: 'Time off removed' })
      setRefresh((n) => n + 1)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/professionals', label: 'Professionals' }}
        title={professional ? `Agenda · ${professional.name}` : 'Agenda'}
        hint={
          professional
            ? `${describeWeek(professional.working_hours)} · ${professional.slot_minutes}-minute slots · capacity ${capacityLabel(professional.daily_limit, professional.monthly_limit)}${clinic ? ` · ${clinic.name}${clinic.address ? `, ${clinic.address}` : ''}` : ''}`
            : ''
        }
      >
        {canEditPricing && <Link className="button ghost" to={`/professionals/${id}`}>Edit working hours</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="stack">
        <div className="card padded">
          <WeekCalendar
            professionalId={Number(id)}
            weekStart={weekStart}
            onWeekChange={setWeekStart}
            refreshKey={refresh}
            onSelect={canManageTimeOff ? blockSlot : undefined}
            selectFull
            emptyHint="No working hours this week. Edit the professional to define them."
          />
          {canManageTimeOff && <p className="muted">Click an available (or full) slot to mark it as time off.</p>}
        </div>
        {canManageTimeOff && (
        <div className="grid">
          <form className="panel" onSubmit={submitTimeOff}>
            <h2>Add time off</h2>
            <label>
              Date *
              <input type="date" value={off.date} required onChange={(event) => setField('date', event.target.value)} />
            </label>
            <label className="check">
              <input type="checkbox" checked={off.wholeDay} onChange={(event) => setField('wholeDay', event.target.checked)} />
              Whole day
            </label>
            {!off.wholeDay && (
              <div className="range">
                <input type="time" step={300} value={off.start} required onChange={(event) => setField('start', event.target.value)} />
                <span>to</span>
                <input type="time" step={300} value={off.end} required onChange={(event) => setField('end', event.target.value)} />
              </div>
            )}
            <label>
              Reason
              <input value={off.reason} maxLength={200} onChange={(event) => setField('reason', event.target.value)} />
            </label>
            <button type="submit">Add time off</button>
          </form>
          <div className="card">
            <div className="card-head">
              <h2>Time off this week</h2>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Hours</th>
                  <th>Reason</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {timeOff.map((item) => (
                  <tr key={item.id}>
                    <td>{formatDate(item.date)}</td>
                    <td>{item.start ? `${item.start}–${item.end}` : 'Whole day'}</td>
                    <td>{item.reason}</td>
                    <td className="actions">
                      <button type="button" className="warn" onClick={() => removeTimeOff(item)}>Remove</button>
                    </td>
                  </tr>
                ))}
                {timeOff.length === 0 && <tr><td colSpan={4} className="muted">No time off this week.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        )}
      </div>
    </section>
  )
}
