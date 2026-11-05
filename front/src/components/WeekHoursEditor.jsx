import { WEEKDAYS } from '../dates'

/** Weekly hours are edited as one list of ranges per weekday. */
export function groupByDay(ranges) {
  const days = WEEKDAYS.map(() => [])
  ranges.forEach((item) => days[item.weekday].push({ start: item.start, end: item.end }))
  return days
}

export function flatten(days) {
  return days.flatMap((ranges, weekday) => ranges.map((range) => ({ weekday, ...range })))
}

/**
 * Editor of a week of HH:mm ranges (`days`: one array of {start, end} per
 * weekday, Monday first). Used for the regular working hours and for the
 * on-call shifts of a professional.
 */
export default function WeekHoursEditor({ days, onChange, emptyLabel = 'Not working', defaultRange = { start: '08:00', end: '12:00' } }) {
  function updateRange(weekday, index, field, value) {
    onChange(days.map((ranges, day) => (day === weekday ? ranges.map((range, i) => (i === index ? { ...range, [field]: value } : range)) : ranges)))
  }

  function addRange(weekday) {
    onChange(
      days.map((ranges, day) => {
        if (day !== weekday) return ranges
        const last = ranges[ranges.length - 1]
        return [...ranges, last ? { start: last.end, end: last.end } : { ...defaultRange }]
      }),
    )
  }

  function removeRange(weekday, index) {
    onChange(days.map((ranges, day) => (day === weekday ? ranges.filter((_, i) => i !== index) : ranges)))
  }

  function copyToWeekdays(weekday) {
    onChange(days.map((ranges, day) => (day < 5 ? days[weekday].map((r) => ({ ...r })) : ranges)))
  }

  return (
    <div className="week-editor">
      {WEEKDAYS.map((label, weekday) => (
        <div key={label} className={`week-day ${days[weekday].length ? '' : 'off'}`}>
          <div className="week-day-head">
            <strong>{label}</strong>
            {days[weekday].length === 0 && <span className="muted">{emptyLabel}</span>}
            <span className="spacer" />
            {weekday < 5 && days[weekday].length > 0 && (
              <button type="button" className="link" onClick={() => copyToWeekdays(weekday)}>
                Copy to Mon–Fri
              </button>
            )}
            <button type="button" className="link" onClick={() => addRange(weekday)}>+ Add range</button>
          </div>
          {days[weekday].map((range, index) => (
            <div key={index} className="range">
              <input type="time" step={300} value={range.start} required onChange={(event) => updateRange(weekday, index, 'start', event.target.value)} />
              <span>to</span>
              <input type="time" step={300} value={range.end} required onChange={(event) => updateRange(weekday, index, 'end', event.target.value)} />
              <button type="button" className="link warn-text" onClick={() => removeRange(weekday, index)}>
                Remove
              </button>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
