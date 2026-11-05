import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { AGREEMENT_BADGE, AGREEMENT_STATUS, headcountLabel, payerLabel, statusLabel } from '../../labels'
import { useSession } from '../../session'

export default function AgreementList() {
  const { canManagePartners } = useSession()
  const [params, setParams] = useSearchParams()
  const partnerId = params.get('partner') || ''
  const status = params.get('status') || ''
  const [rows, setRows] = useState([])
  const [partners, setPartners] = useState([])
  const [error, setError] = useState('')

  async function load() {
    const query = new URLSearchParams()
    if (partnerId) query.set('partner_id', partnerId)
    if (status) query.set('status', status)
    setRows(await api(`/agreements?${query.toString()}`))
  }

  useEffect(() => {
    api('/partners').then(setPartners).catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [partnerId, status]) // eslint-disable-line react-hooks/exhaustive-deps

  function filter(key, value) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  async function remove(row) {
    if (!window.confirm(`Delete agreement "${row.name}"?`)) return
    setError('')
    try {
      await api(`/agreements/${row.id}`, { method: 'DELETE', message: 'Agreement deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Corporate / Agreements"
        title="Corporate agreements"
        hint="Six-month contracts negotiated with a company for its employees, at frozen conditions. For every month billed to fewer employees than the minimum, the company pays the month's discounts back."
      >
        <select value={partnerId} aria-label="Partner" onChange={(event) => filter('partner', event.target.value)}>
          <option value="">All partners</option>
          {partners.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <select value={status} aria-label="Status" onChange={(event) => filter('status', event.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(AGREEMENT_STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <span className="count">{rows.length} records</span>
        {canManagePartners && (
          <Link className="button" to={partnerId ? `/agreements/new?partner=${partnerId}` : '/agreements/new'}>New agreement</Link>
        )}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Agreement</th>
              <th>Contract</th>
              <th>Plans and conditions</th>
              <th>Payer</th>
              <th>This month</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name}</strong>
                  <br />
                  <small className="muted"><Link to={`/partners/${row.partner_id}`}>{row.partner_name}</Link></small>
                </td>
                <td>{formatDate(row.starts_on)} – {formatDate(row.ends_on)}</td>
                <td>
                  {row.plans.map((plan) => (
                    <div key={plan.modality_id}>
                      {plan.modality_name}
                      <small className="muted">
                        {plan.discount_percent > 0 ? ` · ${plan.discount_percent}% off · ${money(plan.value_cents)}/month` : ` · ${money(plan.plan_value_cents)}/month`}
                        {plan.grace_days !== null && ` · ${plan.grace_days === 0 ? 'no' : `${plan.grace_days}-day`} waiting period`}
                      </small>
                    </div>
                  ))}
                </td>
                <td>{payerLabel(row.payer, row.company_share_percent)}</td>
                <td>
                  <span className={`badge ${row.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(row.employees, row.min_employees)}</span>
                  {!row.conditions_met && <><br /><small className="muted">shortfall {money(row.shortfall_cents)} projected</small></>}
                  {row.shortfall_open_cents > 0 && <><br /><small className="warn-text">{money(row.shortfall_open_cents)} to collect</small></>}
                </td>
                <td><span className={`badge ${AGREEMENT_BADGE[row.status]}`}>{statusLabel(AGREEMENT_STATUS, row.status)}</span></td>
                <td className="actions">
                  <Link className="button ghost" to={`/agreements/${row.id}`}>Open</Link>
                  {canManagePartners && row.enrollments_count === 0 && (
                    <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">No agreements.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
