import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, money } from '../../api'
import CompanyInvoices from '../../components/CompanyInvoices'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { AGREEMENT_BADGE, AGREEMENT_STATUS, SUBSCRIPTION_STATUS, graceLabel, headcountLabel, monthLabel, payerLabel, statusLabel } from '../../labels'

/** One of the company's agreements: its terms, the minimum this month, its employees and its invoices. */
export default function PortalAgreement() {
  const { id } = useParams()
  const [record, setRecord] = useState(null)
  const [employees, setEmployees] = useState([])
  const [invoices, setInvoices] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([api(`/portal/agreements/${id}`), api(`/portal/employees?agreement_id=${id}`), api(`/portal/agreements/${id}/invoices`)])
      .then(([row, people, bills]) => {
        if (!active) return
        setRecord(row)
        setEmployees(people)
        setInvoices(bills)
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  if (error) return <p className="error">{error}</p>
  if (!record) return <p className="muted">Loading…</p>
  const canEnroll = ['in_force', 'scheduled'].includes(record.status)

  return (
    <section>
      <PageHeader back={{ to: '/my-agreements', label: 'Agreements' }} title={record.name} hint="Your contract with Santé Saúde.">
        <span className={`badge ${AGREEMENT_BADGE[record.status]}`}>{statusLabel(AGREEMENT_STATUS, record.status)}</span>
        {canEnroll && <Link className="button" to={`/my-employees/new?agreement=${id}`}>Enroll an employee</Link>}
        <Link className="button ghost" to="/my-statement">Statement</Link>
      </PageHeader>
      <div className="grid wide">
        <div className="stack">
          <div className="card padded status-panel" data-testid="portal-agreement">
            <h2>Terms</h2>
            <dl>
              <dt>Contract</dt>
              <dd>{formatDate(record.starts_on)} – {formatDate(record.ends_on)} (six months)</dd>
              <dt>Plans</dt>
              <dd>
                {record.plans.map((plan) => (
                  <div key={plan.modality_id}>
                    {plan.modality_name}: <strong>{money(plan.value_cents)}</strong>/month
                    <small className="muted"> · {plan.discount_percent}% off {money(plan.plan_value_cents)} · waiting period {graceLabel(plan.grace_days, plan.plan_grace_days)}</small>
                  </div>
                ))}
              </dd>
              <dt>Payer</dt>
              <dd>{payerLabel(record.payer, record.company_share_percent)}</dd>
              <dt>Minimum</dt>
              <dd>{record.min_employees} employees a month</dd>
              <dt>{monthLabel(record.reference_period)}</dt>
              <dd>
                <span className={`badge ${record.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(record.employees, record.min_employees)}</span>
                <br />
                <small className="muted">
                  {record.conditions_met
                    ? 'The minimum is met: nothing to pay back this month.'
                    : `${record.missing_employees} short: the month's discounts (${money(record.shortfall_cents)} so far) are paid back once the month ends.`}
                </small>
              </dd>
              {record.shortfall_open_cents > 0 && (
                <>
                  <dt>Shortfall to pay</dt>
                  <dd><strong className="warn-text">{money(record.shortfall_open_cents)}</strong></dd>
                </>
              )}
            </dl>
          </div>
          {record.notes && <p className="notice info">{record.notes}</p>}
        </div>
        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h2>Employees</h2>
              <Link to={`/my-employees?agreement=${id}`}>Manage</Link>
            </div>
            <table>
              <thead><tr><th>Employee</th><th>Plan</th><th>Period</th><th>Monthly</th><th>Status</th></tr></thead>
              <tbody>
                {employees.map((row) => (
                  <tr key={row.subscription_id}>
                    <td>{row.name}</td>
                    <td>{row.modality_name}</td>
                    <td>{formatDate(row.joined_on)} – {formatDate(row.cancelled_on || row.expires_on)}</td>
                    <td>
                      {money(row.monthly_amount_cents)}
                      {row.payer === 'shared' && <><br /><small className="muted">company {row.company_share_percent}% · {money(row.company_monthly_cents)}</small></>}
                    </td>
                    <td><span className={`badge ${row.status}`}>{statusLabel(SUBSCRIPTION_STATUS, row.status)}</span></td>
                  </tr>
                ))}
                {employees.length === 0 && <tr><td colSpan={5} className="muted">Nobody enrolled yet.</td></tr>}
              </tbody>
            </table>
          </div>
          <CompanyInvoices invoices={invoices} payer={record.payer} minEmployees={record.min_employees} basePath={`/portal/agreements/${id}`} onError={setError} />
          <p className="muted">Payments are registered by Santé Saúde when received.</p>
        </div>
      </div>
    </section>
  )
}
