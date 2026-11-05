import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { SUBSCRIPTION_STATUS, allowanceShort, statusLabel } from '../../labels'
import { useSession } from '../../session'

export default function SubscriptionList() {
  const session = useSession()
  const [params] = useSearchParams()
  const sellerFilter = params.get('seller') || ''
  const agreementFilter = params.get('agreement') || ''
  const [rows, setRows] = useState([])
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/subscriptions'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function cancel(row) {
    if (!window.confirm(`Cancel the plan of ${row.customer_name}? Appointments will no longer be allowed.`)) return
    setError('')
    try {
      await api(`/subscriptions/${row.id}/cancel`, { method: 'POST', message: 'Plan cancelled' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function remove(row) {
    if (!window.confirm(`Delete the subscription of ${row.customer_name} and its payment history?`)) return
    setError('')
    try {
      await api(`/subscriptions/${row.id}`, { method: 'DELETE' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const visible = rows
    .filter((row) => !status || row.status === status)
    .filter((row) => !sellerFilter || String(row.seller_id) === sellerFilter)
    .filter((row) => !agreementFilter || String(row.agreement_id) === agreementFilter)
  const hasCurrent = rows.some((row) => row.status !== 'cancelada')
  // An employee's corporate plan is changed through its company, not by the employee.
  const corporateOnly = session.isClient && rows.some((row) => row.status !== 'cancelada' && row.agreement_id)
  // Coordinators follow their team's sales too: the seller column tells whose.
  const showSeller = !session.isClient && (!session.isSeller || session.user.seller_team_size > 0)
  const columns = 8 + (showSeller ? 1 : 0)

  return (
    <section>
      <PageHeader
        crumb={session.isClient ? 'My area / Plan' : session.isSeller ? 'My area / Sales' : 'Care / Subscriptions'}
        title={session.isClient ? 'My plan' : session.isSeller ? 'My sales' : 'Subscriptions'}
        hint={
          session.isClient
            ? 'Your plan must be active (paid up, inside the period, waiting period over) to schedule appointments.'
            : session.isSeller
              ? `The plans you sold${session.user.seller_team_size > 0 ? ' and the ones your team sold' : ''}. Your commission is earned as each installment is paid; payments are registered by the staff.`
              : 'Only customers with an active plan (paid up, inside the period, waiting period over) can schedule.'
        }
      >
        {!session.isClient && (
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {Object.entries(SUBSCRIPTION_STATUS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        )}
        <span className="count">{visible.length} records</span>
        {!corporateOnly && (
          <Link className="button" to="/subscriptions/new">
            {session.isSeller ? 'New sale' : session.isClient && hasCurrent ? 'Upgrade my plan' : 'New subscription'}
          </Link>
        )}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Plan</th>
              {showSeller && <th>Sold by</th>}
              <th>Period</th>
              <th>Monthly</th>
              <th>Payments</th>
              <th>Appointments</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.id}>
                <td>{row.customer_name}</td>
                <td>
                  {row.modality_name}
                  {row.agreement_id && session.isClient && <><br /><small className="muted">Corporate plan · {row.partner_name}</small></>}
                </td>
                {showSeller && (
                  <td>
                    {row.agreement_id ? (
                      <>
                        <span className="badge migrada">Corporate</span> {row.partner_name}
                        <br />
                        <small className="muted">
                          {row.agreement_name}
                          {row.monthly_amount_cents < row.list_amount_cents && ` · ${row.discount_percent}% off`}
                          {row.paid_by_company && ' · paid by the company'}
                        </small>
                      </>
                    ) : row.seller_id ? (
                      <>
                        {row.seller_name}
                        <br />
                        <small className="muted">{row.region_name}{row.discount_percent > 0 && ` · ${row.discount_percent}% off`}</small>
                      </>
                    ) : (
                      <span className="muted">Direct</span>
                    )}
                  </td>
                )}
                <td>{formatDate(row.joined_on)} – {formatDate(row.expires_on)}</td>
                <td>{money(row.monthly_amount_cents)}</td>
                <td>
                  {row.installments_paid}/{row.installments_total} paid
                  {row.installments_overdue > 0 && (
                    <> · <span className="badge overdue">{row.installments_overdue} overdue</span></>
                  )}
                  {row.next_due_on && row.status !== 'cancelada' && (
                    <small className="muted"> · next {formatDate(row.next_due_on)}</small>
                  )}
                </td>
                <td>
                  {row.appointment_limit > 0 && row.usage_window_start ? (
                    <span
                      className={`badge ${row.appointments_remaining > 0 ? 'covered' : 'over-limit'}`}
                      title={`${formatDate(row.usage_window_start)} – ${formatDate(row.usage_window_end)}`}
                    >
                      {row.appointments_used}/{row.appointment_limit} this period
                    </span>
                  ) : (
                    <span className="muted">{allowanceShort(row.appointment_limit, row.appointment_limit_period)}</span>
                  )}
                </td>
                <td>
                  <span className={`badge ${row.status}`}>{statusLabel(SUBSCRIPTION_STATUS, row.status)}</span>
                  {row.pending_change && (
                    <>
                      {' '}
                      <span className="badge pendente" title={row.pending_change.summary}>
                        {row.pending_change.kind === 'cancel' ? 'Cancellation' : 'Change'} awaiting {session.isClient ? 'your' : "the customer's"} confirmation
                      </span>
                    </>
                  )}
                </td>
                <td className="actions">
                  <Link className="button ghost" to={`/subscriptions/${row.id}`}>Open</Link>
                  {row.status !== 'cancelada' && session.canEditSubscriptions && (session.isStaff || !row.agreement_id) && (
                    <button type="button" className="ghost" onClick={() => cancel(row)}>Cancel plan</button>
                  )}
                  {session.isStaff && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {visible.length === 0 && <tr><td colSpan={columns} className="muted">{session.isSeller ? 'No sales yet.' : 'No subscriptions.'}</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
