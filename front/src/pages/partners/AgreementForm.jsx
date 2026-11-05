import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import CompanyInvoices from '../../components/CompanyInvoices'
import PageHeader from '../../components/PageHeader'
import { addDays, formatDate, isoDate, parseIsoDate, today } from '../../dates'
import { AGREEMENT_BADGE, AGREEMENT_STATUS, SUBSCRIPTION_STATUS, graceLabel, headcountLabel, monthLabel, payerLabel, statusLabel } from '../../labels'
import { useSession } from '../../session'
import { toast } from '../../toast'

const empty = { partner_id: '', name: '', starts_on: '', min_employees: '1', payer: 'company', company_share_percent: '60', active: true, notes: '' }
export const CONTRACT_MONTHS = 6

/** Monthly value with the negotiated discount, rounded half up like the API. */
function discounted(cents, percent) {
  const pct = Math.max(0, Math.min(100, Number(percent) || 0))
  return cents - Math.floor((cents * pct + 50) / 100)
}

/** Last day of a six-month contract (the API derives it the same way). */
export function contractEnd(startsOn) {
  if (!startsOn) return ''
  const start = parseIsoDate(startsOn)
  const target = new Date(start.getFullYear(), start.getMonth() + CONTRACT_MONTHS, 1)
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  const same = new Date(target.getFullYear(), target.getMonth(), Math.min(start.getDate(), lastDay))
  same.setDate(same.getDate() - 1)
  return isoDate(same)
}

/** A new contract starts on the first day of next month. */
function nextMonth() {
  const now = today()
  return isoDate(new Date(now.getFullYear(), now.getMonth() + 1, 1))
}

export default function AgreementForm() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const session = useSession()
  const readOnly = !session.canManagePartners
  const [form, setForm] = useState(empty)
  const [offers, setOffers] = useState([]) // [{modality_id, discount_percent, grace_days}]
  const [record, setRecord] = useState(null)
  const [partners, setPartners] = useState([])
  const [plans, setPlans] = useState([])
  const [employees, setEmployees] = useState([])
  const [invoices, setInvoices] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [renewing, setRenewing] = useState(false) // the renewal panel is open
  const [carryEmployees, setCarryEmployees] = useState(true)
  const [splitOpen, setSplitOpen] = useState(false) // the "new split for everyone" panel
  const [split, setSplit] = useState({ percent: '60', from: isoDate(today()), note: '' })

  async function loadRecord() {
    const [row, enrolled, bills] = await Promise.all([
      api(`/agreements/${id}`),
      api(`/subscriptions?agreement_id=${id}`),
      api(`/agreements/${id}/invoices`),
    ])
    setRecord(row)
    setEmployees(enrolled)
    setInvoices(bills)
    return row
  }

  useEffect(() => {
    setError('')
    if (!id) {
      // A new agreement starts blank right away (for the partner it was opened from).
      setRecord(null)
      setEmployees([])
      setInvoices([])
      setForm({ ...empty, starts_on: nextMonth(), partner_id: params.get('partner') || '' })
      setOffers([{ modality_id: '', discount_percent: '0', grace_days: '', company_share_percent: '' }])
    }
    // A superseded load (another id, React's dev double mount) must not
    // overwrite what the user already typed.
    let active = true
    Promise.all([api('/partners'), api('/modalities'), id ? loadRecord() : Promise.resolve(null)])
      .then(([companies, catalog, row]) => {
        if (!active) return
        setPartners(companies)
        setPlans(catalog)
        if (row) {
          setForm({
            partner_id: String(row.partner_id),
            name: row.name,
            starts_on: row.starts_on,
            min_employees: String(row.min_employees),
            payer: row.payer,
            company_share_percent: row.payer === 'shared' ? String(row.company_share_percent) : '60',
            active: row.active,
            notes: row.notes,
          })
          setOffers(
            row.plans.map((item) => ({
              modality_id: String(item.modality_id),
              discount_percent: String(item.discount_percent),
              grace_days: item.grace_days === null ? '' : String(item.grace_days),
              company_share_percent: item.company_share_percent === null ? '' : String(item.company_share_percent),
            })),
          )
        }
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function setOffer(index, field, value) {
    setOffers((current) => current.map((item, i) => (i === index ? { ...item, [field]: value } : item)))
  }

  // Once employees are enrolled the terms are frozen: a renegotiation is a new contract.
  const frozen = Boolean(record && record.terms_frozen)
  const shared = form.payer === 'shared'
  const covered = new Set(offers.map((item) => item.modality_id))
  const available = plans.filter((item) => !covered.has(String(item.id)))
  const enrolled = employees.filter((row) => row.status !== 'cancelada')
  const canEnroll = Boolean(record) && session.canEnrollEmployees && ['in_force', 'scheduled'].includes(record.status)

  async function save(event) {
    event.preventDefault()
    setError('')
    if (offers.length === 0 || offers.some((item) => !item.modality_id)) {
      setError('Choose every plan the agreement covers (at least one).')
      return
    }
    setSaving(true)
    try {
      const saved = await api(id ? `/agreements/${id}` : '/agreements', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Agreement updated' : 'Agreement created — enroll its employees',
        body: {
          ...form,
          partner_id: Number(form.partner_id),
          min_employees: Number(form.min_employees),
          company_share_percent: shared ? Number(form.company_share_percent) : null,
          plans: offers.map((item) => ({
            modality_id: Number(item.modality_id),
            discount_percent: Number(item.discount_percent || 0),
            grace_days: item.grace_days === '' ? null : Number(item.grace_days),
            company_share_percent: shared && item.company_share_percent !== '' && item.company_share_percent !== undefined ? Number(item.company_share_percent) : null,
          })),
        },
      })
      if (id) await loadRecord()
      else navigate(`/agreements/${saved.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function changeSplit(event) {
    event.preventDefault()
    setError('')
    try {
      const result = await api(`/agreements/${id}/share`, {
        method: 'PUT',
        body: { company_share_percent: Number(split.percent), effective_from: split.from, note: split.note },
        message: false,
      })
      setSplitOpen(false)
      toast.success(
        `${result.changed} employee${result.changed === 1 ? '' : 's'}: ${result.company_share_percent}% company / ${100 - result.company_share_percent}% employee from ${formatDate(result.effective_from)}`,
      )
      await loadRecord()
    } catch (err) {
      setError(err.message)
    }
  }

  async function renew() {
    setError('')
    try {
      const renewed = await api(`/agreements/${id}/renew`, {
        method: 'POST',
        body: { carry_employees: carryEmployees },
        message: carryEmployees ? 'Contract renewed — its employees were enrolled in it' : 'Contract renewed',
      })
      setRenewing(false)
      navigate(`/agreements/${renewed.id}`)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/agreements', label: 'Agreements' }}
        title={id ? `${form.name || 'Agreement'}${record ? ` · ${record.partner_name}` : ''}` : 'New corporate agreement'}
        hint="A six-month contract. The employees always get the negotiated conditions (frozen once they are enrolled); for every month billed to fewer employees than the minimum, the company pays Santé Saúde the month's discounts back."
      >
        {record && <span className={`badge ${AGREEMENT_BADGE[record.status]}`}>{statusLabel(AGREEMENT_STATUS, record.status)}</span>}
        {canEnroll && <Link className="button" to={`/subscriptions/new?agreement=${id}`}>Enroll an employee</Link>}
        {record && session.canManagePartners && (
          <button type="button" className="ghost" onClick={() => setRenewing(!renewing)}>Renew for six months</button>
        )}
        {record && record.payer === 'shared' && session.canEnrollEmployees && (
          <button type="button" className="ghost" onClick={() => setSplitOpen(!splitOpen)}>Change the split</button>
        )}
      </PageHeader>
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          {frozen && (
            <p className="notice info" data-testid="terms-frozen">
              Employees are enrolled: the terms are frozen. Only the name, the notes and the activity change; renegotiate with the next contract.
            </p>
          )}
          <fieldset disabled={readOnly}>
            <legend>Contract</legend>
            <label>
              Partner company *
              <select value={form.partner_id} required disabled={frozen} onChange={(event) => set('partner_id', event.target.value)}>
                <option value="">Select</option>
                {partners.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}{item.active ? '' : ' (inactive)'}</option>
                ))}
              </select>
              {partners.length === 0 && <small className="hint"><Link to="/partners/new">Register the company</Link> first.</small>}
            </label>
            <label>
              Name *
              <input value={form.name} required maxLength={120} placeholder="e.g. Acme 2026/2" onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              Starts on *
              <input type="date" value={form.starts_on} required disabled={frozen} onChange={(event) => set('starts_on', event.target.value)} />
              {form.starts_on && (
                <small className="hint" data-testid="contract-end">Six months: until {formatDate(contractEnd(form.starts_on))}. Every enrollment fits in them.</small>
              )}
            </label>
            <label>
              Minimum of employees *
              <input
                type="number"
                min={1}
                value={form.min_employees}
                required
                disabled={frozen}
                onChange={(event) => set('min_employees', event.target.value)}
              />
              <small className="hint">Counted per month (employees with a fee due in it). Below it, the company pays the month's discounts back.</small>
            </label>
            <label>
              Who pays *
              <select value={form.payer} disabled={frozen} onChange={(event) => set('payer', event.target.value)}>
                <option value="company">The company (one invoice a month)</option>
                <option value="shared">Co-payment (the company and the employee split each fee)</option>
                <option value="employee">Each employee (at the negotiated price)</option>
              </select>
            </label>
            {shared && (
              <label>
                Company's part of each fee (%) *
                <input
                  type="number"
                  min={1}
                  max={99}
                  required
                  disabled={frozen}
                  value={form.company_share_percent}
                  onChange={(event) => set('company_share_percent', event.target.value)}
                />
                <small className="hint">
                  The default split of new enrollments (a plan may set its own); the employee pays the rest. The company and each employee may
                  then agree other splits, changing over time.
                </small>
              </label>
            )}
            <label className="check">
              <input type="checkbox" checked={form.active} onChange={(event) => set('active', event.target.checked)} />
              Active (a suspended agreement takes no new enrollments)
            </label>
            <label>
              Notes
              <textarea value={form.notes} rows={2} onChange={(event) => set('notes', event.target.value)} />
            </label>
          </fieldset>
          <fieldset disabled={readOnly || frozen}>
            <legend>Plans and conditions</legend>
            <p className="muted">The plans employees may be enrolled in, the discount on the monthly fee and the waiting period (blank: the plan's own).</p>
            <div className="week-editor">
              {offers.map((item, index) => {
                const plan = plans.find((row) => String(row.id) === item.modality_id)
                return (
                  <div key={index} className="offer">
                    <div className="range">
                      <select value={item.modality_id} required aria-label="Plan" onChange={(event) => setOffer(index, 'modality_id', event.target.value)}>
                        <option value="">Plan</option>
                        {plans
                          .filter((row) => String(row.id) === item.modality_id || !covered.has(String(row.id)))
                          .map((row) => <option key={row.id} value={row.id}>{row.name}{row.enterprise ? ' (enterprise)' : ''}</option>)}
                      </select>
                      {!readOnly && !frozen && offers.length > 1 && (
                        <button type="button" className="link warn-text" onClick={() => setOffers(offers.filter((_, i) => i !== index))}>
                          Remove
                        </button>
                      )}
                    </div>
                    <div className="range">
                      <input
                        value={item.discount_percent}
                        type="number"
                        min={0}
                        max={100}
                        aria-label="Discount (%)"
                        style={{ width: 72 }}
                        onChange={(event) => setOffer(index, 'discount_percent', event.target.value)}
                      />
                      <span>% off</span>
                      <input
                        value={item.grace_days}
                        type="number"
                        min={0}
                        max={365}
                        aria-label="Waiting period (days)"
                        placeholder={plan ? String(plan.effective_grace_days) : '—'}
                        style={{ width: 72 }}
                        onChange={(event) => setOffer(index, 'grace_days', event.target.value)}
                      />
                      <span>days waiting</span>
                    </div>
                    {shared && (
                      <div className="range">
                        <input
                          value={item.company_share_percent ?? ''}
                          type="number"
                          min={0}
                          max={100}
                          aria-label="Company's part (%)"
                          placeholder={form.company_share_percent}
                          style={{ width: 72 }}
                          onChange={(event) => setOffer(index, 'company_share_percent', event.target.value)}
                        />
                        <span>% paid by the company{item.company_share_percent === '' || item.company_share_percent === undefined ? " (the agreement's)" : ''}</span>
                      </div>
                    )}
                    {plan && (
                      <small className="muted">
                        {money(plan.plan_value_cents)} → <strong>{money(discounted(plan.plan_value_cents, item.discount_percent))}</strong>/month
                        {item.grace_days === '' ? ` · the plan's waiting period (${plan.effective_grace_days} days${plan.effective_grace_days !== plan.grace_days ? ', its category' : ''})` : ''}
                        {plan.enterprise && ' · enterprise plan'}
                      </small>
                    )}
                  </div>
                )
              })}
              {!readOnly && !frozen && available.length > 0 && (
                <div>
                  <button type="button" className="link" onClick={() => setOffers([...offers, { modality_id: '', discount_percent: '0', grace_days: '', company_share_percent: '' }])}>
                    + Add plan
                  </button>
                </div>
              )}
            </div>
          </fieldset>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
            <Link className="button ghost" to="/agreements">{readOnly ? 'Back' : 'Cancel'}</Link>
          </div>
        </form>
        <div className="stack">
          {record && splitOpen && (
            <form className="card padded" data-testid="split-panel" onSubmit={changeSplit}>
              <h2>New split for every employee</h2>
              <p className="muted">
                As the company agreed with its employees: from the date on, the open fees are split again (those with a part already paid keep their
                split). To change one employee's split, open its subscription.
              </p>
              <div className="range">
                <input type="number" min={0} max={100} required aria-label="Company's part (%)" style={{ width: 80 }} value={split.percent} onChange={(event) => setSplit({ ...split, percent: event.target.value })} />
                <span>% by the company, from</span>
                <input type="date" required aria-label="From" value={split.from} onChange={(event) => setSplit({ ...split, from: event.target.value })} />
              </div>
              <label>
                Note
                <input value={split.note} maxLength={200} onChange={(event) => setSplit({ ...split, note: event.target.value })} />
              </label>
              <div className="actions">
                <button type="submit">Apply</button>
                <button type="button" className="ghost" onClick={() => setSplitOpen(false)}>Cancel</button>
              </div>
            </form>
          )}
          {record && renewing && (
            <div className="card padded" data-testid="renew-panel">
              <h2>Next contract</h2>
              <p>
                Six more months from <strong>{formatDate(isoDate(addDays(parseIsoDate(record.ends_on), 1)))}</strong> with the same terms. To
                renegotiate, renew without the employees, adjust the new contract, then enroll them.
              </p>
              <label className="check">
                <input type="checkbox" checked={carryEmployees} onChange={(event) => setCarryEmployees(event.target.checked)} />
                Enroll in it every employee enrolled until the end of this contract
              </label>
              <div className="actions">
                <button type="button" onClick={renew}>Renew</button>
                <button type="button" className="ghost" onClick={() => setRenewing(false)}>Cancel</button>
              </div>
            </div>
          )}
          {!record && <p className="muted">Create the agreement, then enroll the company's employees — from here, the subscription form, or the company's own portal.</p>}
          {record && (
            <div className="card padded status-panel" data-testid="agreement-status">
              <h2>Minimum of employees</h2>
              <dl>
                <dt>{monthLabel(record.reference_period)}</dt>
                <dd>
                  <span className={`badge ${record.conditions_met ? 'covered' : 'over-limit'}`}>{headcountLabel(record.employees, record.min_employees)}</span>
                  <small className="muted"> billed this month</small>
                </dd>
                <dt>Shortfall</dt>
                <dd>
                  {record.conditions_met
                    ? 'None this month: the minimum is met.'
                    : `${record.missing_employees} employee${record.missing_employees === 1 ? '' : 's'} short: the company pays the month's discounts back (${money(record.shortfall_cents)} projected).`}
                  {record.shortfall_open_cents > 0 && (
                    <><br /><strong className="warn-text">{money(record.shortfall_open_cents)} of ended months to collect.</strong></>
                  )}
                </dd>
                <dt>Plans</dt>
                <dd>
                  {record.plans.map((plan) => (
                    <div key={plan.modality_id}>
                      {plan.modality_name}
                      <small className="muted">
                        {' · '}{plan.discount_percent}% off ({money(plan.value_cents)}/month) · waiting period {graceLabel(plan.grace_days, plan.plan_grace_days)}
                      </small>
                    </div>
                  ))}
                </dd>
                <dt>Payer</dt>
                <dd>{payerLabel(record.payer, record.company_share_percent)}</dd>
                <dt>Contract</dt>
                <dd>{formatDate(record.starts_on)} – {formatDate(record.ends_on)}</dd>
              </dl>
            </div>
          )}
          {record && (
            <div className="card">
              <div className="card-head">
                <h2>Employees enrolled</h2>
                <span className="muted">{enrolled.length} active enrollment{enrolled.length === 1 ? '' : 's'}</span>
              </div>
              <table>
                <thead>
                  <tr><th>Employee</th><th>Plan</th><th>Period</th><th>Monthly</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {employees.map((row) => (
                    <tr key={row.id}>
                      <td><Link to={`/subscriptions/${row.id}`}>{row.customer_name}</Link></td>
                      <td>{row.modality_name}</td>
                      <td>{formatDate(row.joined_on)} – {formatDate(row.expires_on)}</td>
                      <td>
                        {money(row.monthly_amount_cents)}
                        {row.discount_percent > 0 && <small className="muted"> · {row.discount_percent}% off</small>}
                      </td>
                      <td><span className={`badge ${row.status}`}>{statusLabel(SUBSCRIPTION_STATUS, row.status)}</span></td>
                    </tr>
                  ))}
                  {employees.length === 0 && (
                    <tr><td colSpan={5} className="muted">Nobody enrolled yet.{canEnroll && <> <Link to={`/subscriptions/new?agreement=${id}`}>Enroll the first employee</Link>.</>}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {record && (
            <CompanyInvoices
              invoices={invoices}
              payer={record.payer}
              minEmployees={record.min_employees}
              basePath={`/agreements/${id}`}
              canPay={session.canPayInvoices}
              onChanged={loadRecord}
              onError={setError}
              employeeLink={(line) => `/subscriptions/${line.subscription_id}`}
            />
          )}
        </div>
      </div>
    </section>
  )
}
