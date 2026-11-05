import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate, isoDate, today } from '../../dates'
import { SUBSCRIPTION_STATUS, statusLabel } from '../../labels'

/** The waiting period of an enrollment: its length and until when it runs (or that it is over). */
export function WaitingPeriod({ row }) {
  if (!row.grace_days) return <span className="muted">None</span>
  const running = row.usable_from > isoDate(today())
  return (
    <>
      {row.grace_days} day{row.grace_days === 1 ? '' : 's'}
      <br />
      <small className={running ? 'warn-text' : 'muted'}>
        {running ? `until ${formatDate(row.usable_from)}` : `over since ${formatDate(row.usable_from)}`}
      </small>
    </>
  )
}

/** The company's employees enrolled in its agreements; it enrolls, edits and removes them. */
export default function PortalEmployees() {
  const [params, setParams] = useSearchParams()
  const agreementId = params.get('agreement') || ''
  const [rows, setRows] = useState([])
  const [agreements, setAgreements] = useState([])
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')

  async function load() {
    setRows(await api(`/portal/employees${agreementId ? `?agreement_id=${agreementId}` : ''}`))
  }

  useEffect(() => {
    api('/portal/agreements').then(setAgreements).catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [agreementId]) // eslint-disable-line react-hooks/exhaustive-deps

  function filter(value) {
    const next = new URLSearchParams(params)
    if (value) next.set('agreement', value)
    else next.delete('agreement')
    setParams(next, { replace: true })
  }

  /** Co-payment: the company and the employee agreed a new split, from a date on (today or later). */
  async function changeShare(row) {
    const percent = window.prompt(`The company's part of ${row.name}'s fees (%):`, String(row.company_share_percent))
    if (percent === null || percent === '') return
    const from = window.prompt('From (today or later; the open fees of the periods starting then are split again):', isoDate(today()))
    if (!from) return
    setError('')
    try {
      await api(`/portal/employees/${row.subscription_id}/share`, {
        method: 'PUT',
        body: { company_share_percent: Number(percent), effective_from: from },
        message: `${row.name}: the company pays ${percent}% from ${formatDate(from)} — the employee is told`,
      })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function remove(row) {
    const notStarted = row.joined_on > isoDate(today()) && row.installments_paid === 0
    const question = notStarted
      ? `Withdraw ${row.name}'s enrollment? It has not started: it is removed altogether.`
      : `Last day of ${row.name}'s plan (today or later; the fees of the periods starting after it are no longer owed):`
    let cancelledOn = null
    if (notStarted) {
      if (!window.confirm(question)) return
    } else {
      cancelledOn = window.prompt(question, isoDate(today()))
      if (!cancelledOn) return
    }
    setError('')
    try {
      await api(`/portal/employees/${row.subscription_id}/cancel`, {
        method: 'POST',
        body: cancelledOn ? { cancelled_on: cancelledOn } : {},
        message: notStarted ? 'Enrollment withdrawn' : 'Employee removed from the plan',
      })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const term = search.trim().toLowerCase()
  const visible = term ? rows.filter((row) => `${row.name} ${row.document} ${row.email}`.toLowerCase().includes(term)) : rows
  const canEnroll = agreements.some((item) => ['in_force', 'scheduled'].includes(item.status))

  return (
    <section>
      <PageHeader
        crumb="Company portal / Employees"
        title="Employees"
        hint="Your employees enrolled in your agreements. Open one to edit its data, its plan and its dependents; remove an employee who left the company: the plan ends on the date you give."
      >
        <input type="search" placeholder="Search name, CPF, email…" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select value={agreementId} aria-label="Agreement" onChange={(event) => filter(event.target.value)}>
          <option value="">All agreements</option>
          {agreements.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <span className="count">{visible.length} records</span>
        {canEnroll && <Link className="button" to={`/my-employees/new${agreementId ? `?agreement=${agreementId}` : ''}`}>Enroll an employee</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Agreement · plan</th>
              <th>Period</th>
              <th>Waiting period</th>
              <th>Monthly</th>
              <th>Dependents</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.subscription_id}>
                <td>
                  <Link to={`/my-employees/${row.subscription_id}`}><strong>{row.name}</strong></Link>
                  <br />
                  <small className="muted">{[row.document, row.email].filter(Boolean).join(' · ')}</small>
                </td>
                <td>
                  {row.agreement_name}
                  <br />
                  <small className="muted">{row.modality_name}</small>
                </td>
                <td>
                  {formatDate(row.joined_on)} – {formatDate(row.cancelled_on || row.expires_on)}
                </td>
                <td data-testid="employee-grace">
                  <WaitingPeriod row={row} />
                </td>
                <td>
                  {money(row.monthly_amount_cents)}
                  {row.discount_percent > 0 && <small className="muted"> · {row.discount_percent}% off {money(row.list_amount_cents)}</small>}
                  <br />
                  <small className="muted" data-testid="employee-split">
                    {row.payer === 'shared'
                      ? `company ${row.company_share_percent}% ${money(row.company_monthly_cents)} · employee ${money(row.employee_monthly_cents)}`
                      : row.payer === 'company' ? 'paid by the company' : 'paid by the employee'}
                  </small>
                </td>
                <td>{row.dependents_count}</td>
                <td>
                  <span className={`badge ${row.status}`}>{statusLabel(SUBSCRIPTION_STATUS, row.status)}</span>
                  <br />
                  <small className="muted">{row.installments_paid}/{row.installments_total} fees paid</small>
                </td>
                <td className="actions">
                  <Link className="button ghost" to={`/my-employees/${row.subscription_id}`}>{row.cancelled_on ? 'View' : 'Edit'}</Link>
                  {!row.cancelled_on && row.payer === 'shared' && (
                    <button type="button" className="ghost" onClick={() => changeShare(row)}>Change split</button>
                  )}
                  {!row.cancelled_on && <button type="button" className="ghost" onClick={() => remove(row)}>Remove</button>}
                </td>
              </tr>
            ))}
            {visible.length === 0 && <tr><td colSpan={8} className="muted">No employees enrolled.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
