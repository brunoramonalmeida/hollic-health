import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../api'
import PageHeader from '../components/PageHeader'
import PortalHome from './portal/PortalHome'
import { formatDate, formatDateTime } from '../dates'
import { SUBSCRIPTION_STATUS, allowanceLabel, statusLabel } from '../labels'
import SignaturePanel from '../components/Signature'
import { useSession } from '../session'

function Kpi({ label, value, to, accent, danger, hint }) {
  const body = (
    <>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </>
  )
  const className = `kpi ${accent ? 'accent' : ''} ${danger ? 'danger' : ''}`
  return to ? <Link className={`${className} link-card`} to={to}>{body}</Link> : <div className={className}>{body}</div>
}

/** Home page: the main numbers of the system for the signed-in role — the
 * company portal's own home for a partner company's login. */
export default function Dashboard() {
  const session = useSession()
  return session.isPartner ? <PortalHome /> : <NetworkDashboard />
}

function NetworkDashboard() {
  const session = useSession()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  function load() {
    return api('/dashboard').then(setData).catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
  }, [])

  if (error) return <p className="error">{error}</p>
  if (!data) return <p className="muted">Loading…</p>

  const isClient = session.isClient
  const isProfessional = session.isProfessional
  const monthLabel = new Date().toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })

  if (isProfessional) {
    return (
      <section>
        <PageHeader
          crumb="Home"
          title={`Welcome, ${session.user.professional_name || session.user.name}`}
          hint={`Today is ${formatDate(data.today)}. Sign your appointments after each visit: only signed appointments are completed and paid.`}
        >
          <Link className="button" to="/my-agenda">Open my agenda</Link>
          <Link className="button ghost" to="/my-payouts">My payouts</Link>
        </PageHeader>
        {!session.user.totp_enabled && (
          <p className="notice warn" data-testid="totp-banner">
            To sign appointments and receipts you need your authenticator app. <Link to="/my-security">Set it up in Security</Link>.
          </p>
        )}
        <h2 className="section-title">My agenda</h2>
        <div className="kpis">
          <Kpi label="Today" value={data.appointments_today} to="/my-agenda" hint={`${data.checked_in_today} checked in`} />
          <Kpi label="Next 7 days" value={data.appointments_upcoming_week} to="/my-agenda" />
          <Kpi label="Awaiting my signature" value={data.awaiting_signature} to="/appointments" accent={data.awaiting_signature > 0} hint="visits ended" />
          <Kpi label={`Signed in ${monthLabel}`} value={data.signed_month} to="/my-payouts" />
          <Kpi label="Balance to receive" value={money(data.balance_cents)} to="/my-payouts" accent hint={`earned in ${monthLabel}: ${money(data.payout_month_cents)}`} />
          <Kpi label="Receipts to sign" value={data.receipts_to_sign} to="/my-payouts" accent={data.receipts_to_sign > 0} hint="confirm the money received" />
        </div>
        <div className="grid two" style={{ marginTop: 16 }}>
          <div className="card" data-testid="to-sign">
            <div className="card-head">
              <h2>To sign</h2>
              <Link to="/appointments">See all</Link>
            </div>
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Patient</th>
                  <th>Attendance</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.to_sign.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDateTime(row.marked_at)}</td>
                    <td>{row.patient}</td>
                    <td><span className={`badge att-${row.attendance}`}>{row.attendance === 'checked_out' ? 'Visit ended' : row.attendance === 'no_show' ? 'No-show' : row.attendance}</span></td>
                    <td className="actions">
                      <Link className="button ghost" to={`/appointments/${row.id}`}>Open</Link>
                      <SignaturePanel appointment={{ ...row, can_sign: true }} compact onChange={() => load()} />
                    </td>
                  </tr>
                ))}
                {data.to_sign.length === 0 && <tr><td colSpan={4} className="muted">Nothing awaiting your signature.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="card">
            <div className="card-head">
              <h2>Next appointments</h2>
              <Link to="/appointments">See all</Link>
            </div>
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Patient</th>
                  <th>Clinic</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.next_appointments.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDateTime(row.marked_at)}</td>
                    <td>{row.patient}</td>
                    <td>{row.clinic}</td>
                    <td className="actions"><Link className="button ghost" to={`/appointments/${row.id}`}>Open</Link></td>
                  </tr>
                ))}
                {data.next_appointments.length === 0 && <tr><td colSpan={4} className="muted">No upcoming appointments.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    )
  }

  if (session.isSeller) {
    return (
      <section>
        <PageHeader
          crumb="Home"
          title={`Welcome, ${data.seller_name || session.user.name}`}
          hint={`Today is ${formatDate(data.today)}. You sell in region ${data.region_name} at ${(data.region_commission_bp || 0) / 100}% commission on every plan fee your customers pay.`}
        >
          <Link className="button" to="/subscriptions/new">New sale</Link>
        </PageHeader>
        <h2 className="section-title">My sales</h2>
        <div className="kpis">
          <Kpi label={`Sold in ${monthLabel}`} value={data.sales_month} to="/subscriptions" hint={`${data.sales_total} in total`} />
          <Kpi label="My customers" value={data.customers_count} to="/customers" />
          <Kpi label="Active plans" value={data.subscriptions_active} to="/subscriptions" />
          <Kpi label="Awaiting first payment" value={data.subscriptions_pending} to="/subscriptions" danger={data.subscriptions_pending > 0} hint="the plan only starts once paid" />
          <Kpi label="Overdue plans" value={data.subscriptions_overdue} to="/subscriptions" danger={data.subscriptions_overdue > 0} />
          <Kpi label="Installments due in 7 days" value={data.installments_due_week} hint={`${money(data.installments_due_week_cents)} to remind`} />
          <Kpi label={`Commission in ${monthLabel}`} value={money(data.commission_month_cents)} to="/my-commissions" accent hint={`${money(data.commission_total_cents)} in total`} />
          {data.team_size > 0 && <Kpi label="My team" value={data.team_size} to="/my-team" hint={`${data.team_sales_month} team sales in ${monthLabel}`} />}
          {data.team_size > 0 && <Kpi label={`Override in ${monthLabel}`} value={money(data.override_month_cents)} to="/my-team" accent hint={`${money(data.override_total_cents)} in total`} />}
          <Kpi label="Unread notifications" value={data.unread_notifications} to="/notifications" accent={data.unread_notifications > 0} />
        </div>
        {data.leader_name && <p className="notice info" style={{ marginTop: 12 }}>You report to <strong>{data.leader_name}</strong>, the coordinator of your team.</p>}
        <div className="card" style={{ marginTop: 16 }} data-testid="recent-sales">
          <div className="card-head">
            <h2>Recent sales</h2>
            <Link to="/subscriptions">See all</Link>
          </div>
          <table>
            <thead>
              <tr><th>Customer</th>{data.team_size > 0 && <th>Sold by</th>}<th>Plan</th><th>Joined on</th><th>Monthly</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {data.recent_sales.map((row) => (
                <tr key={row.id}>
                  <td>{row.customer_name}</td>
                  {data.team_size > 0 && <td>{row.seller_name || <span className="muted">me</span>}</td>}
                  <td>{row.modality_name}</td>
                  <td>{formatDate(row.joined_on)}</td>
                  <td>{money(row.monthly_amount_cents)}{row.discount_percent > 0 && <small className="muted"> · {row.discount_percent}% off</small>}</td>
                  <td><span className={`badge ${row.status}`}>{statusLabel(SUBSCRIPTION_STATUS, row.status)}</span></td>
                  <td className="actions"><Link className="button ghost" to={`/subscriptions/${row.id}`}>Open</Link></td>
                </tr>
              ))}
              {data.recent_sales.length === 0 && <tr><td colSpan={data.team_size > 0 ? 7 : 6} className="muted">No sales yet. Register a customer and record your first sale.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    )
  }

  return (
    <section>
      <PageHeader
        crumb="Home"
        title={isClient ? `Welcome, ${session.user.customer_name || session.user.name}` : 'Dashboard'}
        hint={isClient ? 'Your plan, your next appointments and what the clinic told you.' : `Today is ${formatDate(data.today)}.`}
      >
        <Link className="button" to="/appointments/new">New appointment</Link>
      </PageHeader>

      <h2 className="section-title">Appointments</h2>
      <div className="kpis">
        <Kpi label="Today" value={data.appointments_today} to="/appointments" hint={!isClient ? `${data.checked_in_today} checked in` : undefined} />
        <Kpi label="Next 7 days" value={data.appointments_upcoming_week} to="/appointments" />
        <Kpi label={`Completed in ${monthLabel}`} value={data.appointments_completed_month} hint={!isClient ? 'signed by the professional' : undefined} />
        <Kpi label={`Cancelled in ${monthLabel}`} value={data.appointments_cancelled_month} danger={data.appointments_cancelled_month > 0} />
        {!isClient && <Kpi label={`No-shows in ${monthLabel}`} value={data.no_shows_month} danger={data.no_shows_month > 0} to="/reports" />}
        {!isClient && <Kpi label="Awaiting signature" value={data.awaiting_signature} to="/appointments" accent={data.awaiting_signature > 0} hint="visits ended, not signed" />}
        {isClient && <Kpi label="My virtual card" value="Show code" to="/my-card" accent hint="needed to start your appointment" />}
        {isClient && <Kpi label="Unread notifications" value={data.unread_notifications} to="/notifications" accent={data.unread_notifications > 0} />}
      </div>

      {!isClient && (
        <>
          <h2 className="section-title">Plans</h2>
          <div className="kpis">
            <Kpi label="Active plans" value={data.subscriptions_active} to="/subscriptions" />
            <Kpi label="Overdue" value={data.subscriptions_overdue} to="/subscriptions" danger={data.subscriptions_overdue > 0} />
            <Kpi label="Pending first payment" value={data.subscriptions_pending} to="/subscriptions" />
            <Kpi label="Installments due in 7 days" value={data.installments_due_week} hint={money(data.installments_due_week_cents)} />
            {data.agreements_in_force != null && (
              <Kpi
                label="Corporate agreements"
                value={data.agreements_in_force}
                to="/agreements?status=in_force"
                danger={data.agreements_below_minimum > 0}
                hint={
                  data.agreements_below_minimum
                    ? `${data.agreements_below_minimum} below the minimum of employees: list price`
                    : 'all at their minimum of employees'
                }
              />
            )}
          </div>
        </>
      )}

      {session.canSeeBalanceReports && (
        <>
          <h2 className="section-title">Finance · {monthLabel}</h2>
          <div className="kpis">
            <Kpi label="Revenue" value={money(data.revenue_month_cents)} to="/reports" accent hint={`plans ${money(data.plan_revenue_month_cents)}`} />
            <Kpi label="Discounts granted" value={money(data.discounts_month_cents)} />
            <Kpi label="Taxes owed" value={money(data.taxes_month_cents)} to="/reports" />
            <Kpi label="Net" value={money(data.net_month_cents)} />
            <Kpi label="Payouts this month" value={money(data.payouts_month_cents)} to="/payouts" hint={data.receipts_unconfirmed ? `${data.receipts_unconfirmed} receipts unconfirmed` : 'all receipts confirmed'} danger={data.receipts_unconfirmed > 0} />
            <Kpi
              label="Delivery queue"
              value={`${data.queue_pending} pending`}
              to="/queue"
              danger={data.queue_dead > 0}
              hint={data.queue_dead ? `${data.queue_dead} dead — needs attention` : 'healthy'}
            />
          </div>
        </>
      )}

      <div className="grid two" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="card-head">
            <h2>{isClient ? 'Your next appointments' : 'Next appointments'}</h2>
            <Link to="/appointments">See all</Link>
          </div>
          <table>
            <thead>
              <tr>
                <th>When</th>
                {!isClient && <th>Patient</th>}
                <th>Professional</th>
                <th>Where</th>
                <th>Amount</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.next_appointments.map((row) => (
                <tr key={row.id}>
                  <td>{formatDateTime(row.marked_at)}</td>
                  {!isClient && <td>{row.patient}</td>}
                  <td>{row.professional}</td>
                  <td>
                    {row.remote ? (
                      <>
                        <span className="badge remote">Video call</span>
                        {row.meeting_url && (
                          <>
                            <br />
                            <a className="small-link" href={row.meeting_url} target="_blank" rel="noreferrer">Join the call</a>
                          </>
                        )}
                      </>
                    ) : (
                      <>
                        {row.clinic}
                        {row.clinic_maps_url && (
                          <>
                            <br />
                            <a className="small-link" href={row.clinic_maps_url} target="_blank" rel="noreferrer">Directions</a>
                          </>
                        )}
                      </>
                    )}
                  </td>
                  <td>{money(row.amount_cents)}</td>
                  <td className="actions"><Link className="button ghost" to={`/appointments/${row.id}`}>Open</Link></td>
                </tr>
              ))}
              {data.next_appointments.length === 0 && (
                <tr><td colSpan={isClient ? 5 : 6} className="muted">No upcoming appointments.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {isClient && (
          <div className="card padded status-panel">
            <h2>Your plan</h2>
            {data.plan ? (
              <>
                <dl>
                  <dt>Plan</dt><dd>{data.plan.modality_name}</dd>
                  <dt>Status</dt><dd><span className={`badge ${data.plan.status}`}>{statusLabel(SUBSCRIPTION_STATUS, data.plan.status)}</span></dd>
                  <dt>Valid until</dt><dd>{formatDate(data.plan.expires_on)}</dd>
                  <dt>Payments</dt>
                  <dd>
                    {data.plan.installments_paid}/{data.plan.installments_total} paid
                    {data.plan.next_due_on && ` · next due ${formatDate(data.plan.next_due_on)}`}
                  </dd>
                  <dt>Appointments</dt>
                  <dd>
                    {data.plan.appointment_limit > 0
                      ? data.plan.usage_window_start
                        ? `${data.plan.appointments_used} of ${data.plan.appointment_limit} used until ${formatDate(data.plan.usage_window_end)}${data.plan.appointments_remaining === 0 ? ' · new ones pay the full price' : ''}`
                        : allowanceLabel(data.plan.appointment_limit, data.plan.appointment_limit_period)
                      : 'Unlimited'}
                  </dd>
                </dl>
                <p style={{ marginTop: 12 }}><Link className="button ghost" to={`/subscriptions/${data.plan.id}`}>Open plan</Link></p>
              </>
            ) : (
              <p className="notice warn">
                You have no plan. The plan is required to schedule appointments. <Link to="/subscriptions/new">Subscribe</Link>
              </p>
            )}
          </div>
        )}
        {!isClient && (
          <div className="card padded">
            <h2>Shortcuts</h2>
            <div className="shortcuts">
              <Link className="button ghost" to="/appointments/new">Schedule appointment</Link>
              <Link className="button ghost" to="/customers/new">Register customer</Link>
              <Link className="button ghost" to="/subscriptions/new">Sell a plan</Link>
              <Link className="button ghost" to="/professionals">Professionals' agendas</Link>
              {session.canSeeAttendantReport && <Link className="button ghost" to="/reports">Reports</Link>}
              {session.role === 'admin' && <Link className="button ghost" to="/tax-rules">Tax rules</Link>}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
