import assert from 'node:assert/strict'
import { test } from 'node:test'
import { currentAndNext, relative, rowContaining, weekTimes } from '../../src/agenda.js'

const row = (id, marked_at, extra = {}) => ({ id, marked_at, status: 'agendada', attendance: 'awaiting', ...extra })
const at = (hours, minutes = 0) => new Date(2026, 9, 26, hours, minutes)

const day = [
  row(1, '2026-10-26T08:00', { status: 'realizada', attendance: 'completed' }),
  row(2, '2026-10-26T09:00'),
  row(3, '2026-10-26T09:30'),
  row(4, '2026-10-26T10:00', { status: 'cancelada', attendance: 'cancelled' }),
  row(5, '2026-10-27T08:00'),
]

test('the appointment whose slot contains now is current, the following one is next', () => {
  const { current, next } = currentAndNext(day, at(9, 10), 30)
  assert.equal(current.id, 2)
  assert.equal(next.id, 3)
})

test('between slots there is no current appointment, only the next', () => {
  const { current, next } = currentAndNext(day, at(8, 45), 30)
  assert.equal(current, null)
  assert.equal(next.id, 2)
})

test('a started visit stays current past its slot; cancelled ones never count', () => {
  const started = day.map((item) => (item.id === 2 ? { ...item, attendance: 'in_progress' } : item))
  const { current, next } = currentAndNext(started, at(9, 50), 30)
  assert.equal(current.id, 2)
  assert.equal(next.id, 5) // 09:30 already passed, 10:00 is cancelled: tomorrow comes next
})

test('a checked-in patient whose slot began is current', () => {
  const checked = day.map((item) => (item.id === 3 ? { ...item, attendance: 'checked_in' } : item))
  assert.equal(currentAndNext(checked, at(9, 31), 30).current.id, 3)
  assert.equal(currentAndNext(checked, at(9, 10), 30).current.id, 2) // checked in early: not before its slot
})

test('after the last appointment nothing is current or next', () => {
  assert.deepEqual(currentAndNext(day, new Date(2026, 9, 28, 12), 30), { current: null, next: null })
})

test('the week grid is the union of the days and the now row is found', () => {
  const days = [
    { date: '2026-10-26', slots: [{ time: '08:00' }, { time: '08:30' }] },
    { date: '2026-10-27', slots: [{ time: '14:00' }, { time: '14:30' }] },
  ]
  const times = weekTimes(days)
  assert.deepEqual(times, ['08:00', '08:30', '14:00', '14:30'])
  assert.equal(rowContaining(times, '08:40', 30), 1) // the gap belongs to the last slot before it
  assert.equal(rowContaining(times, '14:59', 30), 3)
  assert.equal(rowContaining(times, '15:00', 30), -1)
  assert.equal(rowContaining(times, '07:59', 30), -1)
})

test('relative times read naturally', () => {
  assert.equal(relative(at(9), at(9, 0, 30)), 'now')
  assert.equal(relative(at(9), at(9, 25)), 'in 25 min')
  assert.equal(relative(at(9), at(11, 10)), 'in 2 h 10 min')
  assert.equal(relative(at(9), new Date(2026, 9, 29, 9)), 'in 3 days')
})
