import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { AttendanceBadge } from '../components/Attendance'
import PageHeader from '../components/PageHeader'
import { ACTIVE_STATUSES as ACTIVE, currentAndNext, minutesOf, parseLocal, relative, rowContaining, timeOf, weekTimes } from '../agenda'
import { WEEKDAYS, WEEKDAYS_SHORT, addDays, formatDate, isoDate, parseIsoDate, startOfWeek, today } from '../dates'
import { useSession } from '../session'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

function patientOf(row) {
  return row.dependent_name || row.customer_name
}

function kindBadges(row) {
  return (
    <>
      {row.remote && <span className="badge remote" title="Remote appointment (video call)">remote</span>}
      {row.urgent && <span className="badge urgent" title="Ad-hoc urgent visit">urgent</span>}
      {row.on_call && <span className="badge on-call" title="On-call service">on-call</span>}
    </>
  )
}

function NowPanel({ current, next, now, todayRows }) {
  const remaining = todayRows.filter((row) => row.status === 'agendada' && parseLocal(row.marked_at) > now).length
  const toSign = todayRows.filter((row) => row.awaiting_signature).length
  return (
    <div className="now-panel" data-testid="now-panel">
      <div className={`now-card ${current ? 'live' : ''}`}>
        <span className="now-label">Now</span>
        {current ? (
          <>
            <strong>{current.marked_at.slice(11, 16)} · {patientOf(current)}</strong>
            <small className="muted">
              {current.specialty_name} · {current.remote ? 'video call' : current.clinic_name} {kindBadges(current)} <AttendanceBadge appointment={current} />
            </small>
            <div className="actions">
              <Link className="button" to={`/appointments/${current.id}`}>Open</Link>
              {current.remote && current.meeting_url && <a className="button ghost" href={current.meeting_url} target="_blank" rel="noreferrer">Join the call</a>}
            </div>
          </>
        ) : (
          <strong className="muted">No visit under way</strong>
        )}
      </div>
      <div className="now-card">
        <span className="now-label">Next</span>
        {next ? (
          <>
            <strong>
              {isoDate(parseLocal(next.marked_at)) === isoDate(now) ? next.marked_at.slice(11, 16) : `${formatDate(next.marked_at)} ${next.marked_at.slice(11, 16)}`} · {patientOf(next)}
            </strong>
            <small className="muted">
              {relative(now, parseLocal(next.marked_at))} · {next.specialty_name} · {next.remote ? 'video call' : next.clinic_name} {kindBadges(next)}
            </small>
            <div className="actions">
              <Link className="button ghost" to={`/appointments/${next.id}`}>Open</Link>
            </div>
          </>
        ) : (
          <strong className="muted">Nothing else scheduled</strong>
        )}
      </div>
      <div className="now-card">
        <span className="now-label">Today</span>
        <strong>{todayRows.filter((row) => ACTIVE.has(row.status)).length} appointment{todayRows.filter((row) => ACTIVE.has(row.status)).length === 1 ? '' : 's'}</strong>
        <small className="muted">
          {remaining} still to come{toSign ? ` · ${toSign} awaiting your signature` : ''}
        </small>
        <div className="actions">
          {toSign > 0 && <Link className="button ghost" to="/appointments">To sign</Link>}
        </div>
      </div>
    </div>
  )
}

/** One appointment inside the week grid. */
function AppointmentCard({ row, current, next, selected }) {
  const classes = ['agenda-item', row.status, current ? 'current' : '', next ? 'next' : '', selected ? 'selected' : ''].filter(Boolean).join(' ')
  return (
    <Link className={classes} to={`/appointments/${row.id}`} title={`${patientOf(row)} · ${row.specialty_name}`}>
      <span className="agenda-item-head">
        <strong>{patientOf(row)}</strong>
        {current && <em className="tag-now">now</em>}
        {next && <em className="tag-next">next</em>}
      </span>
      <small>
        {row.remote ? 'video call' : row.clinic_name}
        {row.dependent_name ? ` · dep. of ${row.customer_name}` : ''}
      </small>
      <span className="agenda-item-badges">
        {kindBadges(row)}
        {row.status === 'realizada' ? <span className="badge signed">signed</span> : row.awaiting_signature ? <span className="badge awaiting-signature">to sign</span> : <AttendanceBadge appointment={row} />}
      </span>
    </Link>
  )
}

/**
 * Week view: one column per working day of the professional (days with
 * slots or appointments), one row per slot time. Free slots, time off and
 * full days come from the agenda API; appointments are enriched with their
 * status, kind and attendance. A line marks the current time today.
 */
function WeekView({ professional, now, rows, days, loading, current, next, selectedDay }) {
  const byKey = useMemo(() => {
    const map = new Map()
    for (const row of rows) if (ACTIVE.has(row.status)) map.set(`${row.marked_at.slice(0, 10)}T${row.marked_at.slice(11, 16)}`, row)
    return map
  }, [rows])
  const todayIso = isoDate(today())
  // Working days: the ones with slots (or with an appointment off the grid).
  const visible = days.filter((day) => day.slots.length > 0)
  const columns = visible.length ? visible : days
  const times = useMemo(() => weekTimes(columns), [columns])
  const slotAt = (day, time) => day.slots.find((slot) => slot.time === time)
  const nowTime = timeOf(now)
  const nowRowIndex = rowContaining(times, nowTime, professional.slot_minutes)

  if (!loading && times.length === 0) {
    return <p className="muted">No working hours and no appointments this week.</p>
  }
  return (
    <div className={`agenda-week ${loading ? 'loading' : ''}`} style={{ gridTemplateColumns: `64px repeat(${columns.length}, minmax(0, 1fr))` }} data-testid="agenda-week">
      <div className="agenda-corner" />
      {columns.map((day) => (
        <header key={day.date} className={`agenda-day-head ${day.date === todayIso ? 'today' : ''} ${day.date === selectedDay ? 'selected' : ''}`}>
          <strong>{WEEKDAYS_SHORT[day.weekday]}</strong>
          <span>{formatDate(day.date).slice(0, 5)}</span>
          {day.full && <span className="badge over-limit" title="At capacity">full</span>}
          <small className="muted">{day.slots.filter((slot) => slot.status === 'booked').length} booked</small>
        </header>
      ))}
      {times.map((time, rowIndex) => (
        <div key={time} className="agenda-row" style={{ display: 'contents' }}>
          <div className={`agenda-time ${rowIndex === nowRowIndex && columns.some((day) => day.date === todayIso) ? 'now' : ''}`}>{time}</div>
          {columns.map((day) => {
            const slot = slotAt(day, time)
            const key = `${day.date}T${time}`
            const row = byKey.get(key)
            const isNowCell = day.date === todayIso && rowIndex === nowRowIndex
            const past = day.date < todayIso || (day.date === todayIso && minutesOf(time) + professional.slot_minutes <= minutesOf(nowTime))
            return (
              <div key={key} className={`agenda-cell ${slot ? slot.status : 'none'} ${slot && slot.on_call ? 'on-call' : ''} ${isNowCell ? 'now' : ''} ${past ? 'past' : ''}`}>
                {isNowCell && <i className="now-line" style={{ top: `${Math.min(100, Math.max(0, ((minutesOf(nowTime) - minutesOf(time)) / professional.slot_minutes) * 100))}%` }} />}
                {row ? (
                  <AppointmentCard row={row} current={current && current.id === row.id} next={next && next.id === row.id} />
                ) : slot && slot.status === 'booked' ? (
                  <span className="agenda-item booked-other"><strong>{slot.patient_name}</strong></span>
                ) : slot && slot.status === 'blocked' ? (
                  <span className="agenda-free off">time off</span>
                ) : slot && slot.status === 'full' ? (
                  <span className="agenda-free off">full</span>
                ) : slot ? (
                  <span className="agenda-free">{slot.on_call ? 'free · on call' : 'free'}</span>
                ) : null}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

/** Month view: one cell per day with its appointments; a click opens that week. */
function MonthView({ monthStart, rows, timeOff, onPickDay, current, next }) {
  const todayIso = isoDate(today())
  const first = startOfWeek(monthStart)
  const monthEnd = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0)
  const weeks = []
  for (let cursor = first; cursor <= monthEnd; cursor = addDays(cursor, 7)) weeks.push(cursor)
  const byDay = useMemo(() => {
    const map = new Map()
    for (const row of rows) {
      if (!ACTIVE.has(row.status)) continue
      const day = row.marked_at.slice(0, 10)
      if (!map.has(day)) map.set(day, [])
      map.get(day).push(row)
    }
    for (const list of map.values()) list.sort((a, b) => a.marked_at.localeCompare(b.marked_at))
    return map
  }, [rows])
  const offDays = useMemo(() => new Set(timeOff.filter((item) => !item.start).map((item) => item.date)), [timeOff])

  return (
    <div className="agenda-month" data-testid="agenda-month">
      {WEEKDAYS_SHORT.map((name) => <div key={name} className="agenda-month-head">{name}</div>)}
      {weeks.map((weekStart) =>
        Array.from({ length: 7 }, (_, index) => {
          const date = addDays(weekStart, index)
          const iso = isoDate(date)
          const inMonth = date.getMonth() === monthStart.getMonth()
          const list = byDay.get(iso) || []
          return (
            <button
              type="button"
              key={iso}
              className={`agenda-month-day ${inMonth ? '' : 'outside'} ${iso === todayIso ? 'today' : ''} ${offDays.has(iso) ? 'off' : ''}`}
              onClick={() => onPickDay(iso)}
              title={`Open the week of ${formatDate(iso)}`}
            >
              <span className="day-number">
                {date.getDate()}
                {list.length > 0 && <em className="count">{list.length}</em>}
              </span>
              {offDays.has(iso) && <small className="muted">day off</small>}
              <span className="day-items">
                {list.slice(0, 3).map((row) => (
                  <small key={row.id} className={`day-item ${row.status} ${current && current.id === row.id ? 'current' : ''} ${next && next.id === row.id ? 'next' : ''}`}>
                    {row.marked_at.slice(11, 16)} {patientOf(row)}
                  </small>
                ))}
                {list.length > 3 && <small className="muted">+{list.length - 3} more</small>}
              </span>
            </button>
          )
        }),
      )}
    </div>
  )
}

/**
 * The professional's own agenda: the current and next visits at a glance,
 * the working week as a grid (default) or the whole month.
 */
export default function MyAgenda() {
  const session = useSession()
  const [view, setView] = useState('week')
  const [weekStart, setWeekStart] = useState(() => startOfWeek(today()))
  const [monthStart, setMonthStart] = useState(() => new Date(today().getFullYear(), today().getMonth(), 1))
  const [selectedDay, setSelectedDay] = useState(null)
  const [professional, setProfessional] = useState(null)
  const [rows, setRows] = useState([]) // appointments of the range shown
  const [upcoming, setUpcoming] = useState([]) // today onwards: the "now / next" panel
  const [days, setDays] = useState([]) // slots of the week shown
  const [timeOff, setTimeOff] = useState([])
  const [now, setNow] = useState(() => new Date())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // The clock ticks every 30 s (the "now" line and the current visit); the
  // upcoming appointments are re-read every 2 min so a check-in or a new
  // booking shows up without reloading the page.
  useEffect(() => {
    let ticks = 0
    const timer = setInterval(() => {
      setNow(new Date())
      ticks += 1
      if (ticks % 4 === 0) loadUpcoming().catch(() => {})
    }, 30000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    api('/professionals/me').then(setProfessional).catch((err) => setError(err.message))
  }, [])

  // The "now / next" panel looks from today on, whatever period is shown.
  function loadUpcoming() {
    const start = isoDate(today())
    return api(`/appointments?start=${start}&end=${isoDate(addDays(today(), 60))}`).then(setUpcoming)
  }

  useEffect(() => {
    loadUpcoming().catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    if (!session.professionalId) return undefined
    let active = true
    setLoading(true)
    setError('')
    const start = view === 'week' ? weekStart : startOfWeek(monthStart)
    const end = view === 'week' ? addDays(weekStart, 6) : addDays(startOfWeek(new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0)), 6)
    const range = `start=${isoDate(start)}&end=${isoDate(end)}`
    const requests = [api(`/appointments?${range}`), api(`/professionals/${session.professionalId}/time-off?${range}`)]
    if (view === 'week') requests.push(api(`/professionals/${session.professionalId}/slots?${range}`))
    Promise.all(requests)
      .then(([appointments, off, slots]) => {
        if (!active) return
        setRows(appointments)
        setTimeOff(off)
        setDays(slots || [])
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [session.professionalId, view, weekStart.getTime(), monthStart.getTime()])

  const slotMinutes = professional ? professional.slot_minutes : 30
  const { current, next } = useMemo(() => currentAndNext(upcoming, now, slotMinutes), [upcoming, now, slotMinutes])
  const todayRows = useMemo(() => upcoming.filter((row) => row.marked_at.slice(0, 10) === isoDate(today())), [upcoming])

  function move(direction) {
    if (view === 'week') setWeekStart(addDays(weekStart, 7 * direction))
    else setMonthStart(new Date(monthStart.getFullYear(), monthStart.getMonth() + direction, 1))
  }

  function goToday() {
    setWeekStart(startOfWeek(today()))
    setMonthStart(new Date(today().getFullYear(), today().getMonth(), 1))
    setSelectedDay(null)
  }

  function pickDay(iso) {
    setSelectedDay(iso)
    setWeekStart(startOfWeek(parseIsoDate(iso)))
    setView('week')
  }

  const weekLabel = `${formatDate(isoDate(weekStart))} – ${formatDate(isoDate(addDays(weekStart, 6)))}`
  const monthLabel = `${MONTHS[monthStart.getMonth()]} ${monthStart.getFullYear()}`
  const workingDays = professional ? [...new Set(professional.working_hours.map((range) => range.weekday))].sort() : []

  return (
    <section className="my-agenda">
      <PageHeader
        crumb="My agenda"
        title="My agenda"
        hint={
          professional
            ? `Working ${workingDays.map((day) => WEEKDAYS[day]).join(', ') || 'no fixed days'} · ${professional.slot_minutes}-minute slots · ${professional.clinic_name}`
            : 'Your week at a glance.'
        }
      >
        <Link className="button ghost" to="/appointments">List view</Link>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <NowPanel current={current} next={next} now={now} todayRows={todayRows} />
      <div className="card padded">
        <div className="calendar-nav">
          <div className="view-switch" role="tablist">
            <button type="button" role="tab" aria-selected={view === 'week'} className={view === 'week' ? 'active' : 'ghost'} onClick={() => setView('week')}>Week</button>
            <button type="button" role="tab" aria-selected={view === 'month'} className={view === 'month' ? 'active' : 'ghost'} onClick={() => setView('month')}>Month</button>
          </div>
          <button type="button" className="ghost" onClick={() => move(-1)}>‹ Previous</button>
          <button type="button" className="ghost" onClick={goToday}>Today</button>
          <button type="button" className="ghost" onClick={() => move(1)}>Next ›</button>
          <input
            type="date"
            aria-label="Go to"
            value={view === 'week' ? isoDate(weekStart) : isoDate(monthStart)}
            onChange={(event) => {
              if (!event.target.value) return
              const picked = parseIsoDate(event.target.value)
              setWeekStart(startOfWeek(picked))
              setMonthStart(new Date(picked.getFullYear(), picked.getMonth(), 1))
            }}
          />
          <strong className="period-label" data-testid="period-label">{view === 'week' ? weekLabel : monthLabel}</strong>
          <span className="legend">
            <i className="slot available" /> free <i className="slot booked" /> booked <i className="slot blocked" /> time off{' '}
            <i className="slot available on-call" /> on call
          </span>
        </div>
        {view === 'week' && professional ? (
          <WeekView professional={professional} now={now} rows={rows} days={days} loading={loading} current={current} next={next} selectedDay={selectedDay} />
        ) : view === 'month' ? (
          <MonthView monthStart={monthStart} rows={rows} timeOff={timeOff} onPickDay={pickDay} current={current} next={next} />
        ) : (
          <p className="muted">Loading…</p>
        )}
      </div>
    </section>
  )
}
