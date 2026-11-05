import { useEffect, useState } from 'react'
import { api } from '../api'
import { WEEKDAYS_SHORT, addDays, formatDate, isoDate, parseIsoDate, startOfWeek, today } from '../dates'

/**
 * Loads `/professionals/:id/slots` for the week that starts on `weekStart`.
 * `editingId` is the appointment being moved: its own place does not count
 * against the professional's/clinic's capacity (same rule as the API check).
 */
export function useWeekSlots(professionalId, weekStart, refreshKey = 0, editingId = null) {
  const [days, setDays] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!professionalId) {
      setDays([])
      return undefined
    }
    let active = true
    setLoading(true)
    setError('')
    const start = isoDate(weekStart)
    const end = isoDate(addDays(weekStart, 6))
    const exclude = editingId != null ? `&exclude_appointment=${editingId}` : ''
    api(`/professionals/${professionalId}/slots?start=${start}&end=${end}${exclude}`)
      .then((data) => active && setDays(data))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [professionalId, weekStart.getTime(), refreshKey, editingId])

  return { days, loading, error }
}

function SlotButton({ day, slot, selected, editingId, onSelect, past, selectFull, remote }) {
  const own = slot.appointment_id != null && slot.appointment_id === editingId
  const status = own ? 'available' : slot.status
  // Remote appointments are not accepted in the on-call shifts.
  const inPersonOnly = remote && slot.on_call && status === 'available'
  // "full" slots take no booking, but the agenda page still selects them for time off.
  const clickable = Boolean(onSelect) && !inPersonOnly && (status === 'available' || (selectFull && status === 'full'))
  const classes = [
    'slot',
    status,
    slot.on_call ? 'on-call' : '',
    slot.urgent ? 'urgent' : '',
    slot.remote ? 'remote' : '',
    inPersonOnly ? 'in-person-only' : '',
    selected ? 'selected' : '',
    past ? 'past' : '',
  ].filter(Boolean).join(' ')
  const kind = slot.urgent
    ? 'Ad-hoc urgent visit (urgent rate). '
    : slot.remote
      ? 'Remote appointment (video call). '
      : slot.on_call
        ? 'On-call shift (on-call rates). '
        : ''
  const title =
    kind +
    (status === 'booked'
      ? `Booked: ${slot.patient_name || ''}`
      : status === 'blocked'
        ? 'Time off'
        : status === 'full'
          ? day.full_reason === 'clinic'
            ? "At capacity: the clinic's service limit is reached"
            : "At capacity: the professional's service limit is reached"
          : own
            ? 'Current time of this appointment'
            : inPersonOnly
              ? 'In person only: remote appointments are not accepted during on-call shifts'
              : 'Available')
  return (
    <button
      type="button"
      className={classes}
      title={title}
      disabled={!clickable}
      onClick={() => clickable && onSelect(day.date, slot.time, { ...slot, status })}
    >
      <span>
        {slot.time}
        {slot.on_call && <em className="on-call-mark" aria-label="on-call"> ☾</em>}
        {slot.remote && <em className="remote-mark" aria-label="remote"> ▣</em>}
      </span>
      {status === 'booked' && (
        <small>{slot.urgent ? `urgent · ${slot.patient_name}` : slot.remote ? `remote · ${slot.patient_name}` : slot.patient_name}</small>
      )}
      {status === 'blocked' && <small>off</small>}
      {status === 'full' && <small>full</small>}
      {status === 'available' && slot.on_call && <small>{inPersonOnly ? 'in person only' : 'on call'}</small>}
    </button>
  )
}

/**
 * Week view of a professional's slots.
 *
 * - `selected`: "YYYY-MM-DDTHH:mm" highlighted in the grid.
 * - `editingId`: appointment id whose own slot must stay selectable.
 * - `remote`: booking a remote appointment: the on-call shifts are not selectable.
 * - `onSelect(date, time, slot)`: called when an available slot is clicked; omit for read-only.
 */
export default function WeekCalendar({
  professionalId,
  weekStart,
  onWeekChange,
  selected,
  editingId = null,
  onSelect,
  selectFull = false,
  remote = false,
  refreshKey,
  emptyHint = 'No working hours this week.',
}) {
  const { days, loading, error } = useWeekSlots(professionalId, weekStart, refreshKey, editingId)
  const todayIso = isoDate(today())
  const hasSlots = days.some((day) => day.slots.length > 0)

  return (
    <div className="calendar">
      <div className="calendar-nav">
        <button type="button" className="ghost" onClick={() => onWeekChange(addDays(weekStart, -7))}>
          ‹ Previous
        </button>
        <button type="button" className="ghost" onClick={() => onWeekChange(startOfWeek(today()))}>
          Today
        </button>
        <button type="button" className="ghost" onClick={() => onWeekChange(addDays(weekStart, 7))}>
          Next ›
        </button>
        <input
          type="date"
          aria-label="Go to week of"
          value={isoDate(weekStart)}
          onChange={(event) => event.target.value && onWeekChange(startOfWeek(parseIsoDate(event.target.value)))}
        />
        <span className="muted">
          {formatDate(isoDate(weekStart))} – {formatDate(isoDate(addDays(weekStart, 6)))}
        </span>
        <span className="legend">
          <i className="slot available" /> available <i className="slot booked" /> booked{' '}
          <i className="slot blocked" /> time off <i className="slot full" /> at capacity{' '}
          <i className="slot available on-call" /> on-call shift <i className="slot booked remote" /> remote
        </span>
      </div>
      {error && <p className="error">{error}</p>}
      {!professionalId && <p className="muted">Select a professional to see the agenda.</p>}
      {professionalId && !loading && !hasSlots && <p className="muted">{emptyHint}</p>}
      <div className={`calendar-grid ${loading ? 'loading' : ''}`}>
        {days.map((day) => (
          <div key={day.date} className={`calendar-day ${day.date === todayIso ? 'today' : ''}`}>
            <header>
              <strong>{WEEKDAYS_SHORT[day.weekday]}</strong>
              <span>{formatDate(day.date).slice(0, 5)}</span>
              {day.full && (
                <span
                  className="badge over-limit"
                  title={day.full_reason === 'clinic' ? "The clinic's service capacity is reached" : "The professional's service capacity is reached"}
                >
                  full
                </span>
              )}
            </header>
            {day.slots.map((slot) => (
              <SlotButton
                key={slot.time}
                day={day}
                slot={slot}
                selected={selected === `${day.date}T${slot.time}`}
                editingId={editingId}
                onSelect={onSelect}
                selectFull={selectFull}
                remote={remote}
                past={day.date < todayIso}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
