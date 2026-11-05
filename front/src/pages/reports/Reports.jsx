import { useEffect, useState } from 'react'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import Commissions from './Commissions'
import Payouts from './Payouts'
import { addDays, formatDate, isoDate, startOfWeek, today } from '../../dates'
import { ROLE_LABEL, useSession } from '../../session'
import { allowanceShort } from '../../labels'
import { toast } from '../../toast'

const API_URL = import.meta.env.VITE_API_URL || '/api'

/** Authenticated download (links cannot carry the bearer token). */
async function downloadCsv(path, filename) {
  const response = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
  if (!response.ok) {
    toast.error('Could not export the file.')
    return
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
  toast.success('File exported')
}

function TaxTable({ title, rows, first = 'Name' }) {
  return (
    <div className="card">
      <div className="card-head"><h2>{title}</h2></div>
      <table>
        <thead>
          <tr>
            <th>{first}</th>
            <th>Services</th>
            <th>Base</th>
            <th>Tax</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td>{row.services}</td>
              <td>{money(row.base_cents)}</td>
              <td><strong>{money(row.tax_cents)}</strong></td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={4} className="muted">Nothing in this range.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function defaultFilters() {
  const end = today()
  return {
    granularity: 'week',
    start: isoDate(addDays(startOfWeek(end), -7 * 7)),
    end: isoDate(end),
    professional_id: '',
    clinic_id: '',
  }
}

function query(filters, withAgenda = true) {
  const params = new URLSearchParams({ granularity: filters.granularity, start: filters.start, end: filters.end })
  if (withAgenda && filters.professional_id) params.set('professional_id', filters.professional_id)
  if (withAgenda && filters.clinic_id) params.set('clinic_id', filters.clinic_id)
  return params.toString()
}

const pct = (value) => `${Math.round(value * 100)}%`

/** Appointments booked over the plan's limit (billed at the full price) and what they bill. */
function OverLimit({ count, cents }) {
  if (!count) return <span className="muted">—</span>
  return (
    <>
      {count} <small className="muted">· {money(cents)}</small>
    </>
  )
}

function Breakdown({ title, rows }) {
  return (
    <div className="card">
      <div className="card-head"><h2>{title}</h2></div>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Appointments</th>
            <th>Completed</th>
            <th>Cancelled</th>
            <th>Gross</th>
            <th>Discount</th>
            <th>Over limit</th>
            <th>Amount</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.name}</td>
              <td>{row.appointments}</td>
              <td>{row.completed}</td>
              <td>{row.cancelled}</td>
              <td>{money(row.gross_cents)}</td>
              <td>{money(row.discount_cents)}</td>
              <td><OverLimit count={row.over_limit} cents={row.over_limit_cents} /></td>
              <td><strong>{money(row.amount_cents)}</strong></td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={8} className="muted">Nothing in this range.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function Reports() {
  const session = useSession()
  const [filters, setFilters] = useState(defaultFilters)
  const [professionals, setProfessionals] = useState([])
  const [clinics, setClinics] = useState([])
  const [appointments, setAppointments] = useState(null)
  const [attendants, setAttendants] = useState(null)
  const [plans, setPlans] = useState(null)
  const [taxes, setTaxes] = useState(null)
  const [attendance, setAttendance] = useState(null)
  const [finance, setFinance] = useState(null)
  const [basis, setBasis] = useState('completed')
  const [tab, setTab] = useState(session.canSeeBalanceReports ? 'appointments' : 'attendants')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    Promise.all([api('/professionals'), api('/clinics')])
      .then(([staff, places]) => {
        setProfessionals(staff)
        setClinics(places)
      })
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    const requests = []
    requests.push(session.canSeeBalanceReports ? api(`/reports/appointments?${query(filters)}`) : Promise.resolve(null))
    requests.push(session.canSeeAttendantReport ? api(`/reports/attendants?${query(filters)}`) : Promise.resolve(null))
    requests.push(session.canSeeBalanceReports ? api(`/reports/plans?${query(filters, false)}`) : Promise.resolve(null))
    requests.push(session.canSeeBalanceReports ? api(`/reports/taxes?${query(filters)}&basis=${basis}`) : Promise.resolve(null))
    requests.push(session.canSeeBalanceReports ? api(`/reports/finance?${query(filters, false)}`) : Promise.resolve(null))
    requests.push(session.canSeeAttendantReport ? api(`/reports/attendance?${query(filters)}`) : Promise.resolve(null))
    Promise.all(requests)
      .then(([a, b, c, d, e, f]) => {
        if (!active) return
        setAppointments(a)
        setAttendants(b)
        setPlans(c)
        setTaxes(d)
        setFinance(e)
        setAttendance(f)
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [filters, basis, session.canSeeBalanceReports, session.canSeeAttendantReport])

  function set(field, value) {
    setFilters((current) => ({ ...current, [field]: value }))
  }

  function quick(kind) {
    const end = today()
    if (kind === 'thisWeek') setFilters({ ...filters, granularity: 'week', start: isoDate(startOfWeek(end)), end: isoDate(end) })
    if (kind === 'thisMonth') setFilters({ ...filters, granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth(), 1)), end: isoDate(end) })
    if (kind === 'last8Weeks') setFilters({ ...filters, granularity: 'week', start: isoDate(addDays(startOfWeek(end), -7 * 7)), end: isoDate(end) })
    if (kind === 'last6Months') setFilters({ ...filters, granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth() - 5, 1)), end: isoDate(end) })
  }

  const tabs = []
  if (session.canSeeBalanceReports) tabs.push(['appointments', 'Appointments & balance'])
  if (session.canSeeAttendantReport) tabs.push(['attendants', 'Attendant efficiency'])
  if (session.canSeeAttendantReport) tabs.push(['attendance', 'Attendance audit'])
  if (session.canSeeBalanceReports) tabs.push(['plans', 'Plans & subscriptions'])
  if (session.canSeeBalanceReports) tabs.push(['taxes', 'Taxes'])
  if (session.canSeeBalanceReports) tabs.push(['finance', 'Finance'])
  if (session.canSeeBalanceReports) tabs.push(['payouts', 'Professional payouts'])
  if (session.canSeeCommissions) tabs.push(['commissions', 'Seller commissions'])

  return (
    <section>
      <PageHeader
        crumb="Management / Reports"
        title="Reports"
        hint="Weekly or monthly view. Filters by professional and clinic apply to appointments, attendant activity and professional payouts."
      >
        <div className="actions">
          <button type="button" className="ghost" onClick={() => quick('thisWeek')}>This week</button>
          <button type="button" className="ghost" onClick={() => quick('thisMonth')}>This month</button>
          <button type="button" className="ghost" onClick={() => quick('last8Weeks')}>Last 8 weeks</button>
          <button type="button" className="ghost" onClick={() => quick('last6Months')}>Last 6 months</button>
        </div>
      </PageHeader>
      <div className="panel filters">
        <label>
          Granularity
          <select value={filters.granularity} onChange={(event) => set('granularity', event.target.value)}>
            <option value="week">Weekly</option>
            <option value="month">Monthly</option>
          </select>
        </label>
        <label>
          From
          <input type="date" value={filters.start} onChange={(event) => set('start', event.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={filters.end} onChange={(event) => set('end', event.target.value)} />
        </label>
        <label>
          Professional
          <select value={filters.professional_id} onChange={(event) => set('professional_id', event.target.value)}>
            <option value="">All</option>
            {professionals.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label>
          Clinic
          <select value={filters.clinic_id} onChange={(event) => set('clinic_id', event.target.value)}>
            <option value="">All</option>
            {clinics.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="tabs">
        {tabs.map(([key, label]) => (
          <button key={key} type="button" className={tab === key ? 'tab active' : 'tab'} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <div className={`stack ${loading ? 'loading' : ''}`}>
        {tab === 'appointments' && appointments && (
          <>
            <div className="kpis">
              <div className="kpi"><span>Booked</span><strong>{appointments.totals.scheduled + appointments.totals.completed}</strong></div>
              <div className="kpi"><span>Completed</span><strong>{appointments.totals.completed}</strong></div>
              <div className="kpi"><span>Cancelled</span><strong>{appointments.totals.cancelled}</strong></div>
              <div className="kpi"><span>Gross</span><strong>{money(appointments.totals.gross_cents)}</strong></div>
              <div className="kpi"><span>Discounts</span><strong>{money(appointments.totals.discount_cents)}</strong></div>
              <div className={`kpi ${appointments.totals.over_limit ? 'danger' : ''}`}>
                <span>Over plan limit</span>
                <strong>{appointments.totals.over_limit}</strong>
                <small>{money(appointments.totals.over_limit_cents)} at full price</small>
              </div>
              <div className="kpi">
                <span>On-call services</span>
                <strong>{appointments.totals.on_call}</strong>
                <small>{money(appointments.totals.on_call_cents)} at on-call rates</small>
              </div>
              <div className="kpi">
                <span>Urgent visits</span>
                <strong>{appointments.totals.urgent}</strong>
                <small>{money(appointments.totals.urgent_cents)} at urgent rates</small>
              </div>
              <div className="kpi">
                <span>Remote appointments</span>
                <strong>{appointments.totals.remote}</strong>
                <small>{money(appointments.totals.remote_cents)} at remote rates</small>
              </div>
              <div className="kpi accent"><span>Balance</span><strong>{money(appointments.totals.amount_cents)}</strong></div>
            </div>
            <div className="card">
              <div className="card-head"><h2>Per {filters.granularity}</h2></div>
              <table>
                <thead>
                  <tr>
                    <th>Period</th>
                    <th>Dates</th>
                    <th>Scheduled</th>
                    <th>Completed</th>
                    <th>Cancelled</th>
                    <th>Gross</th>
                    <th>Discount</th>
                    <th>Over limit</th>
                    <th>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {appointments.periods.map((row) => (
                    <tr key={row.period}>
                      <td>{row.period}</td>
                      <td>{formatDate(row.start)} – {formatDate(row.end)}</td>
                      <td>{row.scheduled}</td>
                      <td>{row.completed}</td>
                      <td>{row.cancelled}</td>
                      <td>{money(row.gross_cents)}</td>
                      <td>{money(row.discount_cents)}</td>
                      <td><OverLimit count={row.over_limit} cents={row.over_limit_cents} /></td>
                      <td><strong>{money(row.amount_cents)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="grid two">
              <Breakdown title="By professional" rows={appointments.by_professional} />
              <Breakdown title="By clinic" rows={appointments.by_clinic} />
              <Breakdown title="By specialty" rows={appointments.by_specialty} />
              <Breakdown title="By plan" rows={appointments.by_plan} />
            </div>
          </>
        )}
        {tab === 'attendants' && attendants && (
          <>
            <div className="card">
              <div className="card-head"><h2>Totals in the range</h2></div>
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Role</th>
                    <th>Booked</th>
                    <th>Completed</th>
                    <th>Cancelled</th>
                    <th>Completion</th>
                    <th>Plans sold</th>
                    <th>Payments</th>
                    <th>Collected</th>
                  </tr>
                </thead>
                <tbody>
                  {attendants.totals.map((row) => (
                    <tr key={row.user_id ?? 'system'}>
                      <td>{row.name}</td>
                      <td>{row.role ? ROLE_LABEL[row.role] || row.role : '—'}</td>
                      <td>{row.appointments_booked}</td>
                      <td>{row.appointments_completed}</td>
                      <td>{row.appointments_cancelled}</td>
                      <td>{pct(row.completion_rate)}</td>
                      <td>{row.plans_sold}</td>
                      <td>{row.payments_registered}</td>
                      <td>{money(row.payments_cents)}</td>
                    </tr>
                  ))}
                  {attendants.totals.length === 0 && <tr><td colSpan={9} className="muted">No activity in this range.</td></tr>}
                </tbody>
              </table>
            </div>
            {attendants.periods.filter((p) => p.rows.length).map((period) => (
              <div className="card" key={period.period}>
                <div className="card-head">
                  <h2>{period.period}</h2>
                  <span className="muted">{formatDate(period.start)} – {formatDate(period.end)}</span>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Booked</th>
                      <th>Completed</th>
                      <th>Cancelled</th>
                      <th>Completion</th>
                      <th>Plans sold</th>
                      <th>Payments</th>
                    </tr>
                  </thead>
                  <tbody>
                    {period.rows.map((row) => (
                      <tr key={row.user_id ?? 'system'}>
                        <td>{row.name}</td>
                        <td>{row.appointments_booked}</td>
                        <td>{row.appointments_completed}</td>
                        <td>{row.appointments_cancelled}</td>
                        <td>{pct(row.completion_rate)}</td>
                        <td>{row.plans_sold}</td>
                        <td>{row.payments_registered}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </>
        )}
        {tab === 'plans' && plans && (
          <>
            <div className="kpis">
              <div className="kpi"><span>Active plans</span><strong>{plans.active_subscriptions}</strong></div>
              <div className="kpi"><span>Overdue</span><strong>{plans.overdue_subscriptions}</strong></div>
              <div className="kpi"><span>Pending</span><strong>{plans.pending_subscriptions}</strong></div>
              <div className="kpi"><span>Sold in range</span><strong>{plans.totals.sold}</strong></div>
              <div className="kpi"><span>Cancelled in range</span><strong>{plans.totals.cancelled}</strong></div>
              <div className="kpi accent"><span>Revenue in range</span><strong>{money(plans.totals.revenue_cents)}</strong></div>
              <div className="kpi"><span>Appointments covered</span><strong>{plans.totals.appointments_covered}</strong><small>with the plan discount</small></div>
              <div className={`kpi ${plans.totals.appointments_over_limit ? 'danger' : ''}`}>
                <span>Over plan limit</span>
                <strong>{plans.totals.appointments_over_limit}</strong>
                <small>{money(plans.totals.over_limit_cents)} at full price</small>
              </div>
            </div>
            <div className="grid two">
              <div className="card">
                <div className="card-head"><h2>Per {filters.granularity}</h2></div>
                <table>
                  <thead>
                    <tr>
                      <th>Period</th>
                      <th>Sold</th>
                      <th>Cancelled</th>
                      <th>Payments</th>
                      <th>Revenue</th>
                      <th>Covered</th>
                      <th>Over limit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plans.periods.map((row) => (
                      <tr key={row.period}>
                        <td>{row.period}</td>
                        <td>{row.sold}</td>
                        <td>{row.cancelled}</td>
                        <td>{row.payments}</td>
                        <td><strong>{money(row.revenue_cents)}</strong></td>
                        <td>{row.appointments_covered}</td>
                        <td><OverLimit count={row.appointments_over_limit} cents={row.over_limit_cents} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="card">
                <div className="card-head"><h2>By plan</h2></div>
                <table>
                  <thead>
                    <tr>
                      <th>Plan</th>
                      <th>Allowance</th>
                      <th>Sold</th>
                      <th>Cancelled</th>
                      <th>Payments</th>
                      <th>Revenue</th>
                      <th>Covered</th>
                      <th>Over limit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plans.by_plan.map((row) => (
                      <tr key={row.modality_id}>
                        <td>
                          {row.name}
                          {row.category_name && <><br /><small className="muted">{row.category_name}{row.enterprise ? ' · enterprise' : ''}</small></>}
                        </td>
                        <td>{allowanceShort(row.appointment_limit, row.appointment_limit_period)}</td>
                        <td>{row.sold}</td>
                        <td>{row.cancelled}</td>
                        <td>{row.payments}</td>
                        <td><strong>{money(row.revenue_cents)}</strong></td>
                        <td>{row.appointments_covered}</td>
                        <td><OverLimit count={row.appointments_over_limit} cents={row.over_limit_cents} /></td>
                      </tr>
                    ))}
                    {plans.by_plan.length === 0 && <tr><td colSpan={8} className="muted">No plans.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="card" style={{ marginTop: 16 }} data-testid="report-categories">
              <div className="card-head">
                <h2>By category</h2>
                <span className="muted">Enterprise plans are sold only through company agreements</span>
              </div>
              <table>
                <thead>
                  <tr><th>Category</th><th>Plans</th><th>Sold</th><th>Cancelled</th><th>Payments</th><th>Revenue</th><th>Covered</th><th>Over limit</th></tr>
                </thead>
                <tbody>
                  {plans.by_category.map((row) => (
                    <tr key={row.category_id ?? 'none'}>
                      <td>{row.name} {row.enterprise && <span className="badge enterprise">Enterprise</span>}</td>
                      <td>{row.plans}</td>
                      <td>{row.sold}</td>
                      <td>{row.cancelled}</td>
                      <td>{row.payments}</td>
                      <td><strong>{money(row.revenue_cents)}</strong></td>
                      <td>{row.appointments_covered}</td>
                      <td>{row.appointments_over_limit}</td>
                    </tr>
                  ))}
                  {plans.by_category.length === 0 && <tr><td colSpan={8} className="muted">No plans.</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="card" style={{ marginTop: 16 }} data-testid="report-agreements">
              <div className="card-head">
                <h2>Corporate agreements (B2B)</h2>
                <span className="muted">Employees this month against each minimum; enrollments, fees and shortfalls received in the range</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Agreement</th>
                    <th>Payer</th>
                    <th>This month</th>
                    <th>Enrolled</th>
                    <th>Cancelled</th>
                    <th>Payments</th>
                    <th>Revenue</th>
                    <th>From the company / employees</th>
                    <th>Discount granted</th>
                    <th>Shortfall received</th>
                    <th>Shortfall to collect</th>
                  </tr>
                </thead>
                <tbody>
                  {plans.by_agreement.map((row) => (
                    <tr key={row.agreement_id}>
                      <td>
                        {row.agreement_name}
                        <br />
                        <small className="muted">{row.partner_name}</small>
                      </td>
                      <td>{{ company: 'Company', shared: 'Co-payment', employee: 'Employees' }[row.payer]}</td>
                      <td>
                        <span className={`badge ${row.conditions_met ? 'covered' : 'over-limit'}`}>
                          {row.employees_month} / {row.min_employees}
                        </span>
                      </td>
                      <td>{row.enrolled}</td>
                      <td>{row.cancelled}</td>
                      <td>{row.payments}</td>
                      <td><strong>{money(row.revenue_cents)}</strong></td>
                      <td>{money(row.company_paid_cents)} <small className="muted">/ {money(row.employee_paid_cents)}</small></td>
                      <td>{money(row.discount_cents)}</td>
                      <td>{money(row.shortfall_cents)}</td>
                      <td>{row.shortfall_open_cents > 0 ? <strong className="warn-text">{money(row.shortfall_open_cents)}</strong> : money(0)}</td>
                    </tr>
                  ))}
                  {plans.by_agreement.length === 0 && <tr><td colSpan={11} className="muted">No corporate agreements.</td></tr>}
                </tbody>
              </table>
            </div>
          </>
        )}
        {tab === 'attendance' && attendance && (
          <>
            <div className="kpis">
              <div className="kpi"><span>Scheduled</span><strong>{attendance.totals.scheduled}</strong></div>
              <div className="kpi"><span>Checked in</span><strong>{attendance.totals.checked_in}</strong></div>
              <div className="kpi"><span>Checked out</span><strong>{attendance.totals.checked_out}</strong></div>
              <div className={`kpi ${attendance.totals.no_shows ? 'danger' : ''}`}><span>No-shows</span><strong>{attendance.totals.no_shows}</strong></div>
              <div className="kpi"><span>Completed w/o check-in</span><strong>{attendance.totals.completed_without_checkin}</strong></div>
              <div className="kpi accent"><span>Attendance rate</span><strong>{pct(attendance.totals.attendance_rate)}</strong></div>
            </div>
            <p className="muted">
              Average arrival: {attendance.totals.avg_arrival_minutes == null ? '—' : `${attendance.totals.avg_arrival_minutes > 0 ? '+' : ''}${attendance.totals.avg_arrival_minutes} min vs. the slot`}
              {' · '}Average visit: {attendance.totals.avg_visit_minutes == null ? '—' : `${attendance.totals.avg_visit_minutes} min`}
            </p>
            {[
              ['Per ' + filters.granularity, attendance.periods, 'Period'],
              ['By clinic', attendance.by_clinic, 'Clinic'],
              ['By professional', attendance.by_professional, 'Professional'],
            ].map(([title, rows, first]) => (
              <div className="card" key={title}>
                <div className="card-head"><h2>{title}</h2></div>
                <table>
                  <thead>
                    <tr>
                      <th>{first}</th>
                      <th>Scheduled</th>
                      <th>Checked in</th>
                      <th>Checked out</th>
                      <th>No check-in</th>
                      <th>No-shows</th>
                      <th>Awaiting</th>
                      <th>Cancelled</th>
                      <th>Attendance</th>
                      <th>Arrival</th>
                      <th>Visit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.period}>
                        <td>{row.period}</td>
                        <td>{row.scheduled}</td>
                        <td>{row.checked_in}</td>
                        <td>{row.checked_out}</td>
                        <td>{row.completed_without_checkin}</td>
                        <td>{row.no_shows}</td>
                        <td>{row.awaiting}</td>
                        <td>{row.cancelled}</td>
                        <td>{pct(row.attendance_rate)}</td>
                        <td>{row.avg_arrival_minutes == null ? '—' : `${row.avg_arrival_minutes} min`}</td>
                        <td>{row.avg_visit_minutes == null ? '—' : `${row.avg_visit_minutes} min`}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && <tr><td colSpan={11} className="muted">Nothing in this range.</td></tr>}
                  </tbody>
                </table>
              </div>
            ))}
          </>
        )}
        {tab === 'taxes' && taxes && (
          <>
            <div className="toolbar" style={{ justifyContent: 'space-between' }}>
              <label className="inline">
                Basis
                <select value={basis} onChange={(event) => setBasis(event.target.value)}>
                  <option value="completed">Services rendered (completed appointments + paid plan fees)</option>
                  <option value="all">Include scheduled appointments (projection)</option>
                </select>
              </label>
              <button
                type="button"
                className="ghost"
                onClick={() => downloadCsv(`/reports/taxes/ledger.csv?start=${filters.start}&end=${filters.end}&basis=${basis}`, `taxes_${filters.start}_${filters.end}.csv`)}
              >
                Export ledger (CSV)
              </button>
            </div>
            {(filters.professional_id || filters.clinic_id) && (
              <p className="notice info">With a professional or clinic filter, plan fees are left out (they belong to the company, not to a clinic).</p>
            )}
            <div className="kpis">
              <div className="kpi"><span>Services</span><strong>{taxes.totals.services}</strong></div>
              <div className="kpi"><span>Billed</span><strong>{money(taxes.totals.amount_cents)}</strong></div>
              <div className="kpi accent"><span>Taxes owed</span><strong>{money(taxes.totals.tax_cents)}</strong></div>
              <div className="kpi"><span>Net after taxes</span><strong>{money(taxes.totals.net_cents)}</strong></div>
              <div className="kpi"><span>Effective rate</span><strong>{taxes.totals.amount_cents ? `${((taxes.totals.tax_cents / taxes.totals.amount_cents) * 100).toFixed(2)}%` : '—'}</strong></div>
            </div>
            <div className="card">
              <div className="card-head"><h2>Per {filters.granularity}</h2></div>
              <table>
                <thead>
                  <tr>
                    <th>Period</th>
                    <th>Dates</th>
                    <th>Services</th>
                    <th>Billed</th>
                    {taxes.by_code.map((row) => <th key={row.key}>{row.key}</th>)}
                    <th>Taxes</th>
                    <th>Net</th>
                  </tr>
                </thead>
                <tbody>
                  {taxes.periods.map((row) => (
                    <tr key={row.period}>
                      <td>{row.period}</td>
                      <td>{formatDate(row.start)} – {formatDate(row.end)}</td>
                      <td>{row.services}</td>
                      <td>{money(row.amount_cents)}</td>
                      {taxes.by_code.map((code) => (
                        <td key={code.key}>{money((row.by_code.find((item) => item.key === code.key) || {}).tax_cents || 0)}</td>
                      ))}
                      <td><strong>{money(row.tax_cents)}</strong></td>
                      <td>{money(row.net_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="grid two">
              <TaxTable title="By tax" rows={taxes.by_code} first="Tax" />
              <TaxTable title="By level" rows={taxes.by_level} first="Level" />
              <TaxTable title="By jurisdiction (government reporting)" rows={taxes.by_jurisdiction} first="Jurisdiction" />
              <TaxTable title="By source" rows={taxes.by_source} first="Source" />
            </div>
          </>
        )}
        {tab === 'commissions' && session.canSeeCommissions && <Commissions filters={filters} />}
        {tab === 'payouts' && session.canSeeBalanceReports && (
          <Payouts filters={filters} downloadCsv={downloadCsv} />
        )}
        {tab === 'finance' && finance && (
          <>
            <p className="muted">
              {finance.company.name}{finance.company.cnpj ? ` · CNPJ ${finance.company.cnpj}` : ''}{finance.company.tax_regime ? ` · ${finance.company.tax_regime}` : ''}
              {' '}· revenue is recognised on completed appointments and received plan fees.
            </p>
            <div className="kpis">
              <div className="kpi"><span>Appointments completed</span><strong>{finance.totals.appointments_completed}</strong></div>
              <div className="kpi"><span>Discounts granted</span><strong>{money(finance.totals.appointments_discount_cents)}</strong></div>
              <div className="kpi"><span>Over plan limit</span><strong>{finance.totals.appointments_over_limit}</strong><small>{money(finance.totals.appointments_over_limit_cents)} at full price</small></div>
              <div className="kpi">
                <span>Plan fees received</span>
                <strong>{money(finance.totals.plan_revenue_cents)}</strong>
                {finance.totals.agreement_charges_cents > 0 && <small>incl. {money(finance.totals.agreement_charges_cents)} of corporate shortfalls</small>}
              </div>
              <div className="kpi accent"><span>Revenue</span><strong>{money(finance.totals.revenue_cents)}</strong></div>
              <div className="kpi"><span>Taxes</span><strong>{money(finance.totals.tax_cents)}</strong></div>
              <div className="kpi"><span>Net</span><strong>{money(finance.totals.net_cents)}</strong></div>
            </div>
            <div className="kpis">
              <div className="kpi"><span>Paid to professionals</span><strong>{money(finance.totals.payouts_professionals_cents)}</strong></div>
              <div className="kpi"><span>Paid to clinics</span><strong>{money(finance.totals.payouts_clinics_cents)}</strong></div>
              <div className="kpi"><span>Payouts</span><strong>{money(finance.totals.payouts_cents)}</strong><small>advances + installments, by payment date</small></div>
              <div className="kpi accent"><span>Result after payouts</span><strong>{money(finance.totals.result_cents)}</strong></div>
            </div>
            <div className="card">
              <div className="card-head"><h2>Per {filters.granularity}</h2></div>
              <table>
                <thead>
                  <tr>
                    <th>Period</th>
                    <th>Completed</th>
                    <th>Gross</th>
                    <th>Discounts</th>
                    <th>Over limit</th>
                    <th>Appointments</th>
                    <th>Plan payments</th>
                    <th>Plan fees</th>
                    <th>Revenue</th>
                    <th>Taxes</th>
                    <th>Net</th>
                    <th>Payouts</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {finance.periods.map((row) => (
                    <tr key={row.period}>
                      <td>{row.period}</td>
                      <td>{row.appointments_completed}</td>
                      <td>{money(row.appointments_gross_cents)}</td>
                      <td>{money(row.appointments_discount_cents)}</td>
                      <td><OverLimit count={row.appointments_over_limit} cents={row.appointments_over_limit_cents} /></td>
                      <td>{money(row.appointments_amount_cents)}</td>
                      <td>{row.plan_payments}</td>
                      <td>{money(row.plan_revenue_cents)}</td>
                      <td><strong>{money(row.revenue_cents)}</strong></td>
                      <td>{money(row.tax_cents)}</td>
                      <td><strong>{money(row.net_cents)}</strong></td>
                      <td>{money(row.payouts_cents)}</td>
                      <td><strong>{money(row.result_cents)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
