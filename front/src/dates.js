export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const WEEKDAYS_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const pad = (value) => String(value).padStart(2, '0')

/** Local date -> "YYYY-MM-DD". */
export function isoDate(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** "YYYY-MM-DD" -> local Date (no timezone shift). */
export function parseIsoDate(text) {
  const [year, month, day] = text.split('-').map(Number)
  return new Date(year, month - 1, day)
}

export function addDays(date, amount) {
  const next = new Date(date)
  next.setDate(next.getDate() + amount)
  return next
}

/** Monday of the week that contains `date`. */
export function startOfWeek(date) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  return addDays(day, -((day.getDay() + 6) % 7))
}

export function today() {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}

/** "YYYY-MM-DD" -> "dd/mm/yyyy". */
export function formatDate(text) {
  if (!text) return ''
  const [year, month, day] = text.slice(0, 10).split('-')
  return `${day}/${month}/${year}`
}

/** "YYYY-MM-DDTHH:mm[:ss]" -> "dd/mm/yyyy HH:mm". */
export function formatDateTime(text) {
  if (!text) return ''
  return `${formatDate(text)} ${text.slice(11, 16)}`
}

export function ageOf(birthDate) {
  if (!birthDate) return ''
  const born = parseIsoDate(birthDate)
  const now = today()
  let age = now.getFullYear() - born.getFullYear()
  const beforeBirthday =
    now.getMonth() < born.getMonth() || (now.getMonth() === born.getMonth() && now.getDate() < born.getDate())
  if (beforeBirthday) age -= 1
  return age
}
