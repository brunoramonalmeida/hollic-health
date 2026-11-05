import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { AGREEMENT_BADGE, AGREEMENT_STATUS, headcountLabel, monthLabel, payerLabel, statusLabel } from '../../labels'

/** The company portal's home: its agreements, the minimum this month and what it owes. */
export default function PortalHome() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api('/portal').then(setData).catch((err) => setError(err.message))
  }, [])

  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted">Loading…</p>
  const current = data.agreements.filter((item) => ['in_force', 'scheduled'].includes(item.status))

  return (
    <section>
      <PageHeader
        crumb="Company portal"
        title={data.partner.name}
        hint="Your corporate plans with Santé Saúde: enroll your employees, follow the minimum of employees of each contract and your monthly invoices."
      >
        {current.length > 0 && <Link className="button" to="/my-employees/new">Enroll an employee</Link>}
      </PageHeader>
      <div className="kpis">
        <Link className="kpi link-card" to="/my-employees">
          <span>Employees enrolled</span>
          <strong>{data.employees_enrolled}</strong>
          <small>covered today</small>
        </Link>
        <Link className="kpi link-card" to="/my-agreements">
          <span>Agreements in force</span>
          <strong>{data.partner.agreements_in_force}</strong>
          <small>{data.partner.agreements_count} in total</small>
        </Link>
        <div className={`kpi ${data.company_open_cents > 0 ? 'accent' : ''}`}>
          <span>To pay</span>
          <strong>{money(data.company_open_cents)}</strong>
          <small>invoices of the months started</small>
        </div>
        <div className={`kpi ${data.shortfall_open_cents > 0 ? 'danger' : ''}`}>
          <span>Shortfall to pay</span>
          <strong>{money(data.shortfall_open_cents)}</strong>
          <small>months below the minimum</small>
        </div>
      </div>
      <h2 className="section-title">Agreements</h2>
      <div className="card">
        <table>
          <thead>
            <tr><th>Agreement</th><th>Contract</th><th>Payer</th><th>This month</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {data.agreements.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name}</strong>
                  <br />
                  <small className="muted">{row.plans.map((plan) => `${plan.modality_name} ${plan.discount_percent}% off`).join(' · ')}</small>
                </td>
                <td>{formatDate(row.starts_on)} – {formatDate(row.ends_on)}</td>
                <td>{payerLabel(row.payer, row.company_share_percent)}</td>
                <td>
                  <span className={`badge ${row.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(row.employees, row.min_employees)}</span>
                  <br />
                  <small className="muted">
                    {monthLabel(row.reference_period)}
                    {!row.conditions_met && ` · ${row.missing_employees} short: ${money(row.shortfall_cents)} of discounts to pay back`}
                  </small>
                </td>
                <td><span className={`badge ${AGREEMENT_BADGE[row.status]}`}>{statusLabel(AGREEMENT_STATUS, row.status)}</span></td>
                <td className="actions"><Link className="button ghost" to={`/my-agreements/${row.id}`}>Open</Link></td>
              </tr>
            ))}
            {data.agreements.length === 0 && <tr><td colSpan={6} className="muted">No agreement yet: contact Santé Saúde.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="notice info" style={{ marginTop: 16 }}>
        Your employees always get the negotiated conditions. Each contract requires a minimum of employees per month (those with a fee due in it);
        for a month below it, your company pays back the discounts granted that month, once the month ends.
      </p>
    </section>
  )
}
