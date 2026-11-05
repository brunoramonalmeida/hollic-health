import { useEffect, useState } from 'react'
import { api, fromCents, money, toCents } from '../api'
import PageHeader from '../components/PageHeader'
import { capacityLabel } from '../labels'
import { describeWeek } from './professionals/ProfessionalList'

/**
 * The professional's own profile: the record the network keeps (read-only
 * here — fees, hours and capacity are managed by the staff) and what the
 * professional decides itself: whether it takes ad-hoc urgent visits (and at
 * which rate, at least three times its regular fee) and whether it offers
 * remote appointments (video call, at its remote rate or the regular fee).
 */
export default function MyProfile() {
  const [row, setRow] = useState(null)
  const [form, setForm] = useState({ urgent_enabled: false, urgent_price: '' })
  const [remote, setRemote] = useState({ remote_enabled: false, remote_price: '' })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [savingRemote, setSavingRemote] = useState(false)

  function load() {
    return api('/professionals/me')
      .then((data) => {
        setRow(data)
        setForm({
          urgent_enabled: Boolean(data.urgent_enabled),
          urgent_price: fromCents(Math.max(data.urgent_price_cents || 0, data.urgent_minimum_cents || 0)),
        })
        setRemote({
          remote_enabled: Boolean(data.remote_enabled),
          remote_price: data.remote_price_cents == null ? '' : fromCents(data.remote_price_cents),
        })
      })
      .catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
  }, [])

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const body = { urgent_enabled: form.urgent_enabled }
      if (form.urgent_enabled) body.urgent_price_cents = toCents(form.urgent_price)
      await api('/professionals/me/urgent', {
        method: 'PUT',
        body,
        message: form.urgent_enabled ? 'You now take urgent visits' : 'Urgent visits switched off',
      })
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function saveRemote(event) {
    event.preventDefault()
    setError('')
    setSavingRemote(true)
    try {
      const body = { remote_enabled: remote.remote_enabled }
      // Blank rate: remote appointments pay the regular fee.
      if (String(remote.remote_price).trim() === '') body.same_as_fee = true
      else body.remote_price_cents = toCents(remote.remote_price)
      await api('/professionals/me/remote', {
        method: 'PUT',
        body,
        message: remote.remote_enabled ? 'You now offer remote appointments' : 'Remote appointments switched off',
      })
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingRemote(false)
    }
  }

  if (error && !row) return <p className="error">{error}</p>
  if (!row) return <p className="muted">Loading…</p>

  const belowMinimum = form.urgent_enabled && toCents(form.urgent_price) < row.urgent_minimum_cents

  return (
    <section>
      <PageHeader
        crumb="My agenda / My profile"
        title={row.name}
        hint="Your record in the network. Fees, hours and capacity are managed by the staff; the urgent visits and the remote appointments are your own choice."
      />
      <div className="grid two">
        <div className="panel status-panel">
          <h2>Record</h2>
          <dl>
            <dt>CPF</dt>
            <dd>
              {row.document || <span className="muted">not registered</span>}
              <small className="muted"> · your ICP-Brasil certificate must carry it to sign prescriptions{row.document ? '' : ' (ask the staff to register it)'}</small>
            </dd>
            <dt>Specialty</dt><dd>{row.specialty_name}</dd>
            <dt>Clinic</dt><dd>{row.clinic_name}</dd>
            <dt>Your fee</dt><dd>{money(row.price_cents)}</dd>
            <dt>Working hours</dt><dd>{describeWeek(row.working_hours)} · {row.slot_minutes}-minute slots</dd>
            <dt>On-call</dt>
            <dd>
              {row.on_call_enabled
                ? `${money(row.on_call_price_cents)} per visit · ${row.on_call_hours.length ? describeWeek(row.on_call_hours) : 'no shifts yet'}`
                : 'Not available'}
            </dd>
            <dt>Remote</dt>
            <dd>{row.remote_enabled ? `${money(row.remote_fee_cents)} per video call` : 'In person only'}</dd>
            <dt>Capacity</dt><dd>{capacityLabel(row.daily_limit, row.monthly_limit)}</dd>
          </dl>
        </div>
        <div className="stack">
          <form className="panel" onSubmit={saveRemote}>
            <h2>Remote appointments</h2>
            <p className="muted">
              A remote appointment is a video call booked in your regular working hours like an in-person one
              (never in your on-call shifts). The patient and you receive a link to join; you ask for the virtual card
              code over the call to start the visit and sign it afterwards as usual.
            </p>
            <label className="check">
              <input
                type="checkbox"
                checked={remote.remote_enabled}
                onChange={(event) => setRemote({ ...remote, remote_enabled: event.target.checked })}
              />
              I offer remote appointments (video call)
            </label>
            {remote.remote_enabled && (
              <label>
                Remote rate (R$)
                <input
                  value={remote.remote_price}
                  inputMode="decimal"
                  placeholder={`Same as your fee (${money(row.price_cents)})`}
                  onChange={(event) => setRemote({ ...remote, remote_price: event.target.value })}
                />
                <small className="hint">Paid instead of your fee on remote appointments. Leave blank to receive your regular fee.</small>
              </label>
            )}
            {row.remote_enabled && (
              <p className="notice ok">Currently offering remote appointments at {money(row.remote_fee_cents)}.</p>
            )}
            <div className="actions">
              <button type="submit" disabled={savingRemote}>Save</button>
            </div>
          </form>
          <form className="panel" onSubmit={save}>
            <h2>Urgent visits</h2>
            <p className="muted">
              An ad-hoc urgent visit is a request outside the agenda, at any time — except during your on-call shifts,
              where urgent visits are never accepted. It pays your urgent rate instead of your fee.
            </p>
            <label className="check">
              <input type="checkbox" checked={form.urgent_enabled} onChange={(event) => setForm({ ...form, urgent_enabled: event.target.checked })} />
              I take ad-hoc urgent visits
            </label>
            {form.urgent_enabled && (
              <label>
                Urgent rate (R$)
                <input value={form.urgent_price} inputMode="decimal" required onChange={(event) => setForm({ ...form, urgent_price: event.target.value })} />
                <small className="hint">
                  At least three times your fee: {money(row.urgent_minimum_cents)}.
                  {belowMinimum && <strong className="warn-text"> Raise it to {money(row.urgent_minimum_cents)} or more.</strong>}
                </small>
              </label>
            )}
            {row.urgent_enabled && (
              <p className="notice ok">
                Currently available for urgent visits at {money(row.urgent_price_cents)}.
              </p>
            )}
            {error && <p className="error">{error}</p>}
            <div className="actions">
              <button type="submit" disabled={saving || belowMinimum}>Save</button>
            </div>
          </form>
        </div>
      </div>
    </section>
  )
}
