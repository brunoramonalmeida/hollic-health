import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { AGREEMENT_BADGE, AGREEMENT_STATUS, graceLabel, headcountLabel, payerLabel, statusLabel } from '../../labels'

/** The company's agreements with Santé Saúde (read-only: the terms are negotiated with the staff). */
export default function PortalAgreements() {
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    api('/portal/agreements').then(setRows).catch((err) => setError(err.message))
  }, [])

  return (
    <section>
      <PageHeader
        crumb="Company portal / Agreements"
        title="Agreements"
        hint="Six-month contracts. The conditions are frozen once your employees are enrolled; a renegotiation is the next contract."
      >
        <span className="count">{rows.length} records</span>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr><th>Agreement</th><th>Contract</th><th>Plans and conditions</th><th>Payer</th><th>This month</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td><strong>{row.name}</strong></td>
                <td>{formatDate(row.starts_on)} – {formatDate(row.ends_on)}</td>
                <td>
                  {row.plans.map((plan) => (
                    <div key={plan.modality_id}>
                      {plan.modality_name}
                      <small className="muted">
                        {' · '}{plan.discount_percent}% off · {money(plan.value_cents)}/month · waiting period {graceLabel(plan.grace_days, plan.plan_grace_days)}
                      </small>
                    </div>
                  ))}
                </td>
                <td>{payerLabel(row.payer, row.company_share_percent)}</td>
                <td>
                  <span className={`badge ${row.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(row.employees, row.min_employees)}</span>
                </td>
                <td><span className={`badge ${AGREEMENT_BADGE[row.status]}`}>{statusLabel(AGREEMENT_STATUS, row.status)}</span></td>
                <td className="actions"><Link className="button ghost" to={`/my-agreements/${row.id}`}>Open</Link></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">No agreement yet: contact Santé Saúde.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
