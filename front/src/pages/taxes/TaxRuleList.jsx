import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { useSession } from '../../session'

export const LEVEL_LABEL = { federal: 'Federal', state: 'State', municipal: 'Municipal' }
export const APPLIES_LABEL = { all: 'Appointments and plan fees', appointment: 'Appointments', subscription: 'Plan fees' }

function scope(row) {
  const parts = []
  if (row.state) parts.push(`State ${row.state}`)
  if (row.city) parts.push(`City ${row.city}`)
  if (row.clinic_name) parts.push(`Clinic ${row.clinic_name}`)
  if (row.specialty_name) parts.push(`Specialty ${row.specialty_name}`)
  if (row.modality_name) parts.push(`Plan ${row.modality_name}`)
  return parts.length ? parts.join(' · ') : 'Everywhere'
}

/** Quick simulator: which taxes apply to a service and how much they take. */
function Preview() {
  const [clinics, setClinics] = useState([])
  const [specialties, setSpecialties] = useState([])
  const [form, setForm] = useState({ kind: 'appointment', amount: '100.00', clinic_id: '', specialty_id: '', on: '' })
  const [result, setResult] = useState(null)

  useEffect(() => {
    Promise.all([api('/clinics'), api('/specialties')]).then(([c, s]) => {
      setClinics(c)
      setSpecialties(s)
    })
  }, [])

  useEffect(() => {
    const params = new URLSearchParams({ kind: form.kind, amount_cents: String(Math.round(Number(form.amount.replace(',', '.')) * 100) || 0) })
    if (form.kind === 'appointment' && form.clinic_id) params.set('clinic_id', form.clinic_id)
    if (form.kind === 'appointment' && form.specialty_id) params.set('specialty_id', form.specialty_id)
    if (form.on) params.set('on', form.on)
    api(`/tax-rules/preview?${params}`, { silent: true }).then(setResult).catch(() => setResult(null))
  }, [form])

  const set = (field, value) => setForm((current) => ({ ...current, [field]: value }))

  return (
    <div className="panel">
      <h2>Simulate</h2>
      <div className="fields narrow">
        <label>
          Service
          <select value={form.kind} onChange={(e) => set('kind', e.target.value)}>
            <option value="appointment">Appointment</option>
            <option value="subscription">Plan fee</option>
          </select>
        </label>
        <label>
          Amount (R$)
          <input value={form.amount} inputMode="decimal" onChange={(e) => set('amount', e.target.value)} />
        </label>
        {form.kind === 'appointment' && (
          <>
            <label>
              Clinic
              <select value={form.clinic_id} onChange={(e) => set('clinic_id', e.target.value)}>
                <option value="">—</option>
                {clinics.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.city}/{c.state || '?'})</option>)}
              </select>
            </label>
            <label>
              Specialty
              <select value={form.specialty_id} onChange={(e) => set('specialty_id', e.target.value)}>
                <option value="">—</option>
                {specialties.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          </>
        )}
        <label>
          Service date
          <input type="date" value={form.on} onChange={(e) => set('on', e.target.value)} />
        </label>
      </div>
      {result && (
        <dl className="quote-lines">
          {result.lines.map((line) => (
            <div key={line.code}>
              <dt>{line.name} ({line.code}) · {LEVEL_LABEL[line.level]} · {(line.rate_bp / 100).toFixed(2).replace(/\.?0+$/, '')}%{line.city ? ` · ${line.city}/${line.state}` : ''}</dt>
              <dd>{money(line.amount_cents)}</dd>
            </div>
          ))}
          <div className="total"><dt>Total taxes</dt><dd>{money(result.tax_cents)}</dd></div>
          <div><dt>Net after taxes</dt><dd>{money(result.net_cents)}</dd></div>
          {result.lines.length === 0 && <p className="muted">No tax rule applies.</p>}
        </dl>
      )}
    </div>
  )
}

export default function TaxRuleList() {
  const session = useSession()
  const canEdit = session.role === 'admin'
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/tax-rules'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete rule ${row.code} ${row.rate_label} (${scope(row)})? Rules already used by booked services are only deactivated.`)) return
    try {
      await api(`/tax-rules/${row.id}`, { method: 'DELETE', message: 'Tax rule removed' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Finance / Tax rules"
        title="Tax rules"
        hint="For each tax code the most specific rule matching the service (state, city, clinic, specialty, plan) applies. Taxes are snapshotted on each service when it is booked or paid."
      >
        <Link className="button ghost" to="/company">Company settings</Link>
        {canEdit && <Link className="button" to="/tax-rules/new">New rule</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="grid wide">
        <Preview />
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Tax</th>
                <th>Level</th>
                <th>Rate</th>
                <th>Applies to</th>
                <th>Scope</th>
                <th>Effective</th>
                <th>Base</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={row.active ? '' : 'inactive'}>
                  <td>
                    <strong>{row.code}</strong> <small className="muted">{row.name}</small>
                    {!row.active && <span className="badge none" style={{ marginLeft: 6 }}>inactive</span>}
                  </td>
                  <td>{LEVEL_LABEL[row.level]}</td>
                  <td>{row.rate_label}</td>
                  <td>{APPLIES_LABEL[row.applies_to]}</td>
                  <td>{scope(row)}</td>
                  <td>{formatDate(row.effective_from)}{row.effective_to ? ` – ${formatDate(row.effective_to)}` : ' →'}</td>
                  <td>{row.base === 'gross' ? 'Gross price' : 'Billed amount'}</td>
                  <td className="actions">
                    <Link className="button ghost" to={`/tax-rules/${row.id}`}>{canEdit ? 'Edit' : 'View'}</Link>
                    {canEdit && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="muted">No tax rules. Without rules no tax is computed.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
