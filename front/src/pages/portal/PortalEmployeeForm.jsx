import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate, isoDate, today } from '../../dates'
import { SUBSCRIPTION_STATUS, payerLabel, statusLabel } from '../../labels'
import { WaitingPeriod } from './PortalEmployees'

const KINSHIPS = ['cônjuge', 'filho(a)', 'enteado(a)', 'pai', 'mãe', 'outro']
const SEXES = { '': '—', F: 'Female', M: 'Male', O: 'Other' }
const emptyDependent = { name: '', birth_date: '', kinship: '', document: '', sex: '' }

/**
 * One employee in the company portal: its identification and contact, its
 * plan (one the agreement covers) and — before the enrollment starts — its
 * start; and its dependents. Never its health data: Santé Saúde and the
 * employee keep that.
 */
export default function PortalEmployeeForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [record, setRecord] = useState(null)
  const [agreement, setAgreement] = useState(null)
  const [form, setForm] = useState(null)
  const [dependent, setDependent] = useState(null) // the dependent being added ({...}) or edited ({id, ...})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  function apply(row) {
    setRecord(row)
    setForm({
      name: row.name,
      document: row.document,
      birth_date: row.birth_date || '',
      email: row.email,
      phone: row.phone,
      modality_id: String(row.modality_id),
      joined_on: row.joined_on,
    })
  }

  useEffect(() => {
    let active = true
    api(`/portal/employees/${id}`)
      .then(async (row) => {
        const terms = await api(`/portal/agreements/${row.agreement_id}`)
        if (!active) return
        apply(row)
        setAgreement(terms)
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  if (error && !record) return <p className="error">{error}</p>
  if (!record || !form) return <p className="muted">Loading…</p>

  const todayIso = isoDate(today())
  // The company manages an employee while it is enrolled.
  const readOnly = Boolean(record.cancelled_on) || record.expires_on < todayIso
  const started = record.joined_on <= todayIso || record.installments_paid > 0
  const plan = agreement ? agreement.plans.find((item) => String(item.modality_id) === form.modality_id) : null
  const extra = Math.max(record.dependents_count - record.dependent_limit, 0)

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const saved = await api(`/portal/employees/${id}`, {
        method: 'PUT',
        message: 'Employee updated — the employee is told',
        body: {
          ...form,
          birth_date: form.birth_date || null,
          modality_id: Number(form.modality_id),
          joined_on: form.joined_on,
        },
      })
      apply(saved)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function saveDependent(event) {
    event.preventDefault()
    setError('')
    const body = { ...dependent }
    delete body.id
    try {
      const saved = await api(dependent.id ? `/portal/employees/${id}/dependents/${dependent.id}` : `/portal/employees/${id}/dependents`, {
        method: dependent.id ? 'PUT' : 'POST',
        message: dependent.id ? 'Dependent updated' : 'Dependent added — the fees follow',
        body,
      })
      apply(saved)
      setDependent(null)
    } catch (err) {
      setError(err.message)
    }
  }

  async function removeDependent(row) {
    if (!window.confirm(`Remove ${row.name} from ${record.name}'s plan? The fees follow.`)) return
    setError('')
    try {
      apply(await api(`/portal/employees/${id}/dependents/${row.id}`, { method: 'DELETE', message: 'Dependent removed' }))
    } catch (err) {
      setError(err.message)
    }
  }

  async function removeEmployee() {
    const notStarted = record.joined_on > todayIso && record.installments_paid === 0
    let body = {}
    if (notStarted) {
      if (!window.confirm(`Withdraw ${record.name}'s enrollment? It has not started: it is removed altogether.`)) return
    } else {
      const cancelledOn = window.prompt(`Last day of ${record.name}'s plan (today or later):`, todayIso)
      if (!cancelledOn) return
      body = { cancelled_on: cancelledOn }
    }
    try {
      await api(`/portal/employees/${id}/cancel`, { method: 'POST', body, message: notStarted ? 'Enrollment withdrawn' : 'Employee removed from the plan' })
      navigate('/my-employees')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/my-employees', label: 'Employees' }}
        title={record.name}
        hint="Correct the employee's data, move it to another plan of the agreement and manage its dependents. The employee is told of every change. Health data stays between the employee and Santé Saúde."
      >
        <span className={`badge ${record.status}`}>{statusLabel(SUBSCRIPTION_STATUS, record.status)}</span>
        {!readOnly && <button type="button" className="warn" onClick={removeEmployee}>Remove from the plan</button>}
      </PageHeader>
      {readOnly && <p className="notice info">This enrollment is {record.cancelled_on ? 'cancelled' : 'over'}: the employee is no longer yours to manage.</p>}
      <div className="grid wide">
        <form className="panel" onSubmit={save} data-testid="employee-form">
          <fieldset disabled={readOnly}>
            <legend>Employee</legend>
            <label>
              Full name *
              <input value={form.name} required maxLength={120} onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              CPF *
              <input value={form.document} required maxLength={20} onChange={(event) => set('document', event.target.value)} />
            </label>
            <label>
              Birth date
              <input type="date" value={form.birth_date} max={todayIso} onChange={(event) => set('birth_date', event.target.value)} />
            </label>
            <label>
              Email
              <input type="email" value={form.email} maxLength={180} onChange={(event) => set('email', event.target.value)} />
            </label>
            <label>
              Phone
              <input value={form.phone} maxLength={30} onChange={(event) => set('phone', event.target.value)} />
            </label>
          </fieldset>
          <fieldset disabled={readOnly}>
            <legend>Plan</legend>
            <label>
              Plan *
              <select value={form.modality_id} onChange={(event) => set('modality_id', event.target.value)}>
                {(agreement ? agreement.plans : []).map((item) => (
                  <option key={item.modality_id} value={item.modality_id}>
                    {item.modality_name} — {money(item.value_cents)}/month ({item.discount_percent}% off)
                  </option>
                ))}
              </select>
              {plan && String(plan.modality_id) !== String(record.modality_id) && (
                <small className="hint">The open fees follow the new plan's negotiated conditions; the paid ones stay.</small>
              )}
            </label>
            <label>
              Starts on
              <input
                type="date"
                value={form.joined_on}
                disabled={started}
                min={todayIso}
                max={record.expires_on}
                onChange={(event) => set('joined_on', event.target.value)}
              />
              <small className="hint">{started ? 'The enrollment has started.' : 'It has not started yet: today or later.'} Until {formatDate(record.expires_on)}, the end of the contract.</small>
            </label>
          </fieldset>
          {error && <p className="error">{error}</p>}
          {!readOnly && (
            <div className="actions">
              <button type="submit" disabled={saving}>Save</button>
              <Link className="button ghost" to="/my-employees">Back</Link>
            </div>
          )}
        </form>
        <div className="stack">
          <div className="card padded status-panel">
            <h2>Enrollment</h2>
            <dl>
              <dt>Agreement</dt>
              <dd>{record.agreement_name}</dd>
              <dt>Period</dt>
              <dd>{formatDate(record.joined_on)} – {formatDate(record.cancelled_on || record.expires_on)}</dd>
              <dt>Waiting period</dt>
              <dd><WaitingPeriod row={record} /></dd>
              <dt>Monthly</dt>
              <dd>
                <strong>{money(record.monthly_amount_cents)}</strong>
                <small className="muted"> · {record.discount_percent}% off {money(record.list_amount_cents)}</small>
                <br />
                <small className="muted">
                  {payerLabel(record.payer, record.company_share_percent)}
                  {record.payer === 'shared' && ` — company ${money(record.company_monthly_cents)}, employee ${money(record.employee_monthly_cents)}`}
                </small>
              </dd>
              <dt>Fees</dt>
              <dd>{record.installments_paid}/{record.installments_total} paid</dd>
            </dl>
          </div>
          <div className="card" data-testid="dependents">
            <div className="card-head">
              <h2>Dependents</h2>
              {!readOnly && !dependent && (
                <button type="button" className="ghost" onClick={() => setDependent({ ...emptyDependent })}>Add dependent</button>
              )}
            </div>
            <p className="muted" style={{ padding: '0 16px' }}>
              {record.dependent_limit} included in the plan; each one above costs {money(record.extra_dependent_cents)} a month (before the negotiated discount).
              {extra > 0 && ` ${extra} above the limit now.`}
            </p>
            <table>
              <thead>
                <tr><th>Name</th><th>Kinship</th><th>Birth date</th><th>CPF</th><th>Sex</th><th></th></tr>
              </thead>
              <tbody>
                {record.dependents.map((row) => (
                  <tr key={row.id}>
                    <td>{row.name}</td>
                    <td>{row.kinship || '—'}</td>
                    <td>{formatDate(row.birth_date)}</td>
                    <td>{row.document || '—'}</td>
                    <td>{SEXES[row.sex] || row.sex}</td>
                    <td className="actions">
                      {!readOnly && (
                        <>
                          <button type="button" className="ghost" onClick={() => setDependent({ ...row, birth_date: row.birth_date || '' })}>Edit</button>
                          <button type="button" className="ghost" onClick={() => removeDependent(row)}>Remove</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
                {record.dependents.length === 0 && <tr><td colSpan={6} className="muted">No dependents.</td></tr>}
              </tbody>
            </table>
            {dependent && (
              <form className="padded" onSubmit={saveDependent} data-testid="dependent-form">
                <h3>{dependent.id ? `Edit ${dependent.name}` : 'New dependent'}</h3>
                <div className="fields two">
                  <label>
                    Name *
                    <input value={dependent.name} required maxLength={120} onChange={(event) => setDependent({ ...dependent, name: event.target.value })} />
                  </label>
                  <label>
                    Birth date *
                    <input type="date" value={dependent.birth_date} required max={todayIso} onChange={(event) => setDependent({ ...dependent, birth_date: event.target.value })} />
                  </label>
                  <label>
                    Kinship
                    <select value={dependent.kinship} onChange={(event) => setDependent({ ...dependent, kinship: event.target.value })}>
                      <option value="">—</option>
                      {KINSHIPS.map((item) => <option key={item} value={item}>{item}</option>)}
                    </select>
                  </label>
                  <label>
                    CPF
                    <input value={dependent.document} maxLength={20} onChange={(event) => setDependent({ ...dependent, document: event.target.value })} />
                  </label>
                  <label>
                    Sex
                    <select value={dependent.sex} onChange={(event) => setDependent({ ...dependent, sex: event.target.value })}>
                      {Object.entries(SEXES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </label>
                </div>
                <div className="actions">
                  <button type="submit">{dependent.id ? 'Save dependent' : 'Add'}</button>
                  <button type="button" className="ghost" onClick={() => setDependent(null)}>Cancel</button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
