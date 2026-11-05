// Pure helpers of the professional's agenda (no React): which appointment is
// under way, which comes next, and the slot grid of a week.

export const ACTIVE_STATUSES = new Set(['agendada', 'realizada'])

export function minutesOf(time) {
  const [hours, minutes] = time.split(':').map(Number)
  return hours * 60 + minutes
}

export function timeOf(date) {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** "YYYY-MM-DDTHH:mm[:ss]" -> local Date (the API stores wall-clock times). */
export function parseLocal(text) {
  const [year, month, day] = text.slice(0, 10).split('-').map(Number)
  const [hours, minutes] = text.slice(11, 16).split(':').map(Number)
  return new Date(year, month - 1, day, hours, minutes, 0, 0)
}

/** "in 25 min", "in 2 h 10 min", "in 3 days", "now". */
export function relative(from, to) {
  const minutes = Math.round((to - from) / 60000)
  if (minutes < 1) return 'now'
  if (minutes < 60) return `in ${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `in ${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`
  const days = Math.round(hours / 24)
  return `in ${days} day${days === 1 ? '' : 's'}`
}

/**
 * What the professional is doing now and next.
 *
 * `current`: the visit under way — one that was started with the patient's
 * card (or checked in and whose slot began), else the scheduled appointment
 * whose slot contains `now`. `next`: the first scheduled appointment still
 * to come (any day). Cancelled appointments never count.
 */
export function currentAndNext(rows, now, slotMinutes) {
  const active = rows.filter((row) => ACTIVE_STATUSES.has(row.status)).sort((a, b) => a.marked_at.localeCompare(b.marked_at))
  const started = active.find(
    (row) => row.status === 'agendada' && (row.attendance === 'in_progress' || (row.attendance === 'checked_in' && parseLocal(row.marked_at) <= now)),
  )
  const current =
    started ||
    active.find((row) => {
      if (row.status !== 'agendada') return false
      const start = parseLocal(row.marked_at)
      return start <= now && now < new Date(start.getTime() + slotMinutes * 60000)
    }) ||
    null
  const next = active.find((row) => row.status === 'agendada' && parseLocal(row.marked_at) > now && row !== current) || null
  return { current, next }
}

/** The sorted slot times of a week: the union of every day's grid. */
export function weekTimes(days) {
  const set = new Set()
  for (const day of days) for (const slot of day.slots) set.add(slot.time)
  return [...set].sort((a, b) => minutesOf(a) - minutesOf(b))
}

/** The row of the grid that contains `time` ("HH:mm"), or -1 before the first / after the last slot ends. */
export function rowContaining(times, time, slotMinutes) {
  const minute = minutesOf(time)
  return times.findIndex((item, index) => {
    const next = times[index + 1]
    const end = next ? minutesOf(next) : minutesOf(item) + slotMinutes
    return minutesOf(item) <= minute && minute < end
  })
}
