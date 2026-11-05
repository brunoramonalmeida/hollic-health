import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { addDays, formatDate, isoDate, parseIsoDate, today } from '../../dates'
import { INSTALLMENT_STATUS, PAYER, SUBSCRIPTION_STATUS, allowanceLabel, headcountLabel, monthLabel, payerLabel, statusLabel } from '../../labels'
import { Part } from '../../components/CompanyInvoices'
import { useSession } from '../../session'

const empty = { customer_id: '', modality_id: '', joined_on: '', expires_on: '', seller_id: '', agreement_id: '', company_share_percent: '', upgrade: false }

/** joined_on + 12 months - 1 day: the usual one-year plan. */
function defaultExpiration(joinedOn) {
  const start = parseIsoDate(joinedOn)
  const next = new Date(start.getFullYear() + 1, start.getMonth(), start.getDate())
  return isoDate(addDays(next, -1))
}

export default function SubscriptionForm() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const session = useSession()
  const [form, setForm] = useState(empty)
  const [record, setRecord] = useState(null)
  const [customers, setCustomers] = useState([])
  const [modalities, setModalities] = useState([])
  const [dependents, setDependents] = useState(0)
  const [sellers, setSellers] = useState([])
  const [regions, setRegions] = useState([])
  const [agreements, setAgreements] = useState([]) // corporate agreements (staff only)
  const [quote, setQuote] = useState(null)
  const [current, setCurrent] = useState(null) // the customer's plan in force (a new one upgrades it)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function loadRecord() {
    applyRecord(await api(`/subscriptions/${id}`))
  }

  function applyRecord(row) {
    setRecord(row)
    setForm({
      customer_id: String(row.customer_id),
      modality_id: String(row.modality_id),
      joined_on: row.joined_on,
      expires_on: row.expires_on,
      seller_id: row.seller_id ? String(row.seller_id) : '',
      agreement_id: row.agreement_id ? String(row.agreement_id) : '',
    })
  }

  useEffect(() => {
    setError('')
    // Sellers and regions are staff data: clients never attribute a sale. A
    // seller login only knows itself and its own region (and sells as itself).
    const sales = session.canRecordSellerSales
      ? [api('/sellers'), api('/regions')]
      : session.isSeller
        ? [api('/sellers/me').then((me) => [me]), Promise.resolve(null)]
        : [Promise.resolve([]), Promise.resolve([])]
    // Corporate agreements: only the staff enrolls employees.
    const corporate = session.canEnrollEmployees ? api('/agreements') : Promise.resolve([])
    // Ignore a load that was superseded (another id, or React's dev double
    // mount): a late answer must not reset what the user already picked.
    let active = true
    Promise.all([api('/customers'), api('/modalities'), ...sales, corporate, id ? api(`/subscriptions/${id}`) : Promise.resolve(null)])
      .then(([people, plans, team, areas, contracts, row]) => {
        if (!active) return
        // Only the load still current fills the form (never a superseded one, over what was typed).
        if (row) applyRecord(row)
        setCustomers(people)
        setModalities(plans)
        setSellers(team)
        setRegions(areas || team.map((item) => item.region))
        setAgreements(contracts)
        if (!id) {
          setRecord(null)
          // Opened from an agreement ("Enroll an employee"): inside its period.
          const chosen = contracts.find((item) => String(item.id) === params.get('agreement'))
          const joinedOn = chosen && chosen.starts_on > isoDate(today()) ? chosen.starts_on : isoDate(today())
          const expiresOn = defaultExpiration(joinedOn)
          setForm({
            ...empty,
            customer_id: session.isClient ? String(session.customerId) : params.get('customer') || '',
            seller_id: session.isSeller ? String(session.sellerId) : '',
            agreement_id: chosen ? String(chosen.id) : '',
            modality_id: chosen && chosen.plans.length === 1 ? String(chosen.plans[0].modality_id) : '',
            joined_on: joinedOn,
            expires_on: chosen && expiresOn > chosen.ends_on ? chosen.ends_on : expiresOn,
          })
        }
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  useEffect(() => {
    if (!form.customer_id) {
      setDependents(0)
      setCurrent(null)
      return
    }
    api(`/customers/${form.customer_id}/dependents`)
      .then((rows) => setDependents(rows.length))
      .catch(() => setDependents(0))
    // A new subscription for a customer who already has one is a plan upgrade.
    if (!id) {
      api(`/subscriptions/customer/${form.customer_id}`, { silent: true })
        .then((row) => setCurrent(row))
        .catch(() => setCurrent(null))
    }
  }, [form.customer_id, id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const plan = modalities.find((item) => String(item.id) === form.modality_id)
  const extra = plan ? Math.max(dependents - plan.dependent_limit, 0) : 0
  const monthly = plan ? plan.plan_value_cents + extra * plan.extra_dependent_cents : 0
  const locked = record && record.status === 'cancelada'
  const pending = record ? record.pending_change : null
  // A corporate enrollment is managed by the staff on the company's word:
  // the employee sees it, but changes go through its company.
  const companyManaged = Boolean(record && record.agreement_id) && !session.isStaff
  // A seller changes or cancels its own sale through a request the customer
  // confirms; while one is pending the sale is frozen for it.
  const requesting = Boolean(record) && !locked && session.canRequestSaleChanges && record.seller_id === session.sellerId
  const readOnly = (Boolean(record) && !session.canEditSubscriptions && (!requesting || Boolean(pending))) || companyManaged
  const seller = sellers.find((item) => String(item.id) === form.seller_id)
  const region = seller ? regions.find((item) => item.id === seller.region_id) : null
  const offer = (modalityId) => (region ? region.plans.find((item) => String(item.modality_id) === String(modalityId)) : null)
  // Corporate enrollment: the agreement's plans and conditions, never a seller.
  const agreement = agreements.find((item) => String(item.id) === form.agreement_id)
  const agreementOffer = (modalityId) =>
    agreement ? agreement.plans.find((item) => String(item.modality_id) === String(modalityId)) : null
  // Agreements offered: those taking enrollments, plus the current one whatever its state.
  const agreementOptions = agreements.filter(
    (item) => ['in_force', 'scheduled'].includes(item.status) || (record && item.id === record.agreement_id),
  )
  // A sale is checked against the seller's region when it is new or its
  // seller/plan changes; otherwise the terms it was sold with are kept. A
  // corporate enrollment follows the agreement's live terms.
  const saleChanged =
    !record ||
    form.seller_id !== (record.seller_id ? String(record.seller_id) : '') ||
    form.modality_id !== String(record.modality_id) ||
    form.agreement_id !== (record.agreement_id ? String(record.agreement_id) : '')
  // Sellers offered: the active ones, plus the current one even if inactive now.
  const sellerOptions = sellers.filter((item) => item.active || (record && item.id === record.seller_id))

  useEffect(() => {
    // No quote on a sale the viewer cannot change (e.g. a coordinator reading a team sale).
    if (!form.customer_id || !form.modality_id || readOnly) {
      setQuote(null)
      return
    }
    let active = true
    const query = new URLSearchParams({ customer_id: form.customer_id, modality_id: form.modality_id })
    // Only the staff (or the seller itself) quotes a seller's sale; the API refuses it to clients.
    if (form.seller_id && session.canSellPlans && !form.agreement_id) query.set('seller_id', form.seller_id)
    // Corporate enrollment (staff): the agreement's conditions and its minimum on the join day.
    if (form.agreement_id && session.canEnrollEmployees) {
      query.set('agreement_id', form.agreement_id)
      if (form.joined_on) query.set('joined_on', form.joined_on)
      if (form.expires_on) query.set('expires_on', form.expires_on)
    }
    api(`/subscriptions/quote?${query.toString()}`, { silent: true })
      .then((body) => active && setQuote(body))
      .catch(() => active && setQuote(null))
    return () => {
      active = false
    }
  }, [form.customer_id, form.modality_id, form.seller_id, form.agreement_id, form.joined_on, form.expires_on, dependents, readOnly])

  const blocked = saleChanged && quote && !quote.allowed
  // Upgrading: the customer's current plan ends the day before the new one starts.
  // Only a plan overlapping the new one is replaced; one that ends before it is simply the previous plan.
  const upgrading = !id && current && current.status !== 'cancelada' && (!form.joined_on || current.expires_on >= form.joined_on)
  // The employee's corporate plan is changed by the staff on the company's word.
  const upgradeManaged = upgrading && Boolean(current.agreement_id) && !session.isStaff
  const upgradeBlocked = upgrading && (upgradeManaged || !form.upgrade || (form.joined_on && form.joined_on <= current.joined_on))
  // The seller's commission per installment: internal (the API leaves it null for clients).
  const showCommission = Boolean(record && record.seller_id && record.commission_bp != null)
  const nextOpen = record ? record.installments.find((item) => !item.paid_on) : null
  // A co-payment: each fee has the company's part and the employee's, paid on their own.
  const split = Boolean(record && record.payer === 'shared')

  async function run(action) {
    setError('')
    try {
      await action()
    } catch (err) {
      setError(err.message)
    }
  }

  async function requestEdit() {
    const changes = {}
    if (form.modality_id !== String(record.modality_id)) changes.modality_id = Number(form.modality_id)
    if (form.joined_on !== record.joined_on) changes.joined_on = form.joined_on
    if (form.expires_on !== record.expires_on) changes.expires_on = form.expires_on
    if (Object.keys(changes).length === 0) {
      setError('Nothing changed.')
      return
    }
    const note = window.prompt('Reason for the change (shown to the customer, optional):', '')
    if (note === null) return
    await api(`/subscriptions/${id}/changes`, {
      method: 'POST',
      body: { kind: 'edit', ...changes, note },
      message: 'Change requested — nothing changes until the customer confirms it',
    })
    await loadRecord()
  }

  function requestCancellation() {
    const cancelledOn = window.prompt('Cancellation date (the customer must confirm it):', isoDate(today()))
    if (!cancelledOn) return
    const note = window.prompt('Reason (shown to the customer, optional):', '')
    if (note === null) return
    run(async () => {
      await api(`/subscriptions/${id}/changes`, {
        method: 'POST',
        body: { kind: 'cancel', cancelled_on: cancelledOn, note },
        message: 'Cancellation requested — the plan stays until the customer confirms it',
      })
      await loadRecord()
    })
  }

  function withdrawRequest() {
    if (!window.confirm('Withdraw your request? The customer will no longer be asked.')) return
    run(async () => {
      await api(`/subscriptions/${id}/changes/${pending.id}`, { method: 'DELETE', message: 'Request withdrawn' })
      await loadRecord()
    })
  }

  function decideRequest(decision) {
    const what = pending.kind === 'cancel' ? 'cancellation' : 'change'
    const who = session.isClient ? 'you' : 'the customer'
    if (!window.confirm(decision === 'confirm' ? `Confirm the ${what}? It is applied at once.` : `Reject the ${what}? The plan stays as it is.`)) return
    const note = window.prompt(`Note for the seller (optional)${session.isClient ? '' : ` — recorded as ${who}'s answer`}:`, '')
    if (note === null) return
    run(async () => {
      await api(`/subscriptions/${id}/changes/${pending.id}/${decision}`, {
        method: 'POST',
        body: { note },
        message: decision === 'confirm' ? `${what[0].toUpperCase()}${what.slice(1)} confirmed and applied` : `${what[0].toUpperCase()}${what.slice(1)} rejected`,
      })
      await loadRecord()
    })
  }

  async function save(event) {
    event.preventDefault()
    setSaving(true)
    if (requesting) {
      await run(requestEdit)
      setSaving(false)
      return
    }
    await run(async () => {
      const saved = await api(id ? `/subscriptions/${id}` : '/subscriptions', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Subscription updated' : session.isSeller ? 'Sale recorded — installments generated' : 'Subscription created — installments generated',
        body: {
          customer_id: Number(form.customer_id),
          modality_id: Number(form.modality_id),
          joined_on: form.joined_on,
          expires_on: form.expires_on,
          // Only the staff attributes a sale (a seller sells as itself); clients leave it as it is.
          ...(session.canSellPlans ? { seller_id: form.seller_id && !form.agreement_id ? Number(form.seller_id) : null } : {}),
          // Only the staff enrolls in (or takes out of) a corporate agreement.
          ...(session.canEnrollEmployees ? { agreement_id: form.agreement_id ? Number(form.agreement_id) : null } : {}),
          // Co-payment: the split the company and the employee agreed (new enrollments; then "Change split").
          ...(!id && agreement && agreement.payer === 'shared' && form.company_share_percent !== ''
            ? { company_share_percent: Number(form.company_share_percent) }
            : {}),
          ...(upgrading ? { upgrade: true } : {}),
        },
      })
      // Stay on the record after creating: the first payment is usually registered right away.
      if (id) navigate('/subscriptions')
      else navigate(`/subscriptions/${saved.id}`)
    })
    setSaving(false)
  }

  function pay(item) {
    const paidOn = window.prompt(`Payment date for installment ${item.sequence} (${money(item.amount_cents)})`, isoDate(today()))
    if (!paidOn) return
    run(async () => {
      await api(`/subscriptions/${id}/installments/${item.id}`, { method: 'PUT', body: { paid_on: paidOn }, message: `Payment of installment ${item.sequence} registered` })
      await loadRecord()
    })
  }

  /** Co-payment: register (or undo) the company's or the employee's part of a fee. */
  function payPart(item, part) {
    const cents = part === 'company' ? item.company_share_cents : item.employee_share_cents
    const paidOn = window.prompt(`Payment date of the ${part}'s part of installment ${item.sequence} (${money(cents)})`, isoDate(today()))
    if (!paidOn) return
    run(async () => {
      await api(`/subscriptions/${id}/installments/${item.id}`, { method: 'PUT', body: { paid_on: paidOn, part }, message: `The ${part}'s part of installment ${item.sequence} registered` })
      await loadRecord()
    })
  }

  function undoPart(item, part) {
    if (!window.confirm(`Undo the ${part}'s part of installment ${item.sequence}?`)) return
    run(async () => {
      await api(`/subscriptions/${id}/installments/${item.id}`, { method: 'PUT', body: { paid_on: null, part }, message: 'Payment undone' })
      await loadRecord()
    })
  }

  /** Co-payment: the company and the employee agreed a new split. */
  function changeShare() {
    const percent = window.prompt("The company's part of the employee's fees (%), as they agreed:", String(record.company_share_percent ?? ''))
    if (percent === null || percent === '') return
    const from = window.prompt('From (the open fees of the periods starting then are split again):', isoDate(today()))
    if (!from) return
    run(async () => {
      await api(`/subscriptions/${id}/share`, { method: 'PUT', body: { company_share_percent: Number(percent), effective_from: from }, message: 'Split changed' })
      await loadRecord()
    })
  }

  function undoPayment(item) {
    if (!window.confirm(`Undo the payment of installment ${item.sequence}?`)) return
    run(async () => {
      await api(`/subscriptions/${id}/installments/${item.id}`, { method: 'PUT', body: { paid_on: null }, message: 'Payment undone' })
      await loadRecord()
    })
  }

  function cancelPlan() {
    if (!window.confirm('Cancel this plan? The customer will no longer be able to schedule.')) return
    run(async () => {
      await api(`/subscriptions/${id}/cancel`, { method: 'POST', message: 'Plan cancelled' })
      await loadRecord()
    })
  }

  function reactivate() {
    run(async () => {
      await api(`/subscriptions/${id}/reactivate`, { method: 'POST', message: 'Plan reactivated' })
      await loadRecord()
    })
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/subscriptions', label: session.isSeller ? 'My sales' : 'Subscriptions' }}
        title={id ? `${session.isSeller ? 'Sale' : 'Subscription'} · ${record ? record.customer_name : ''}` : session.isSeller ? 'New sale' : 'New subscription'}
        hint={
          session.isSeller
            ? 'The sale follows the rules of your region: only its plans, only to customers inside its scope, at its special discount. The plan is billed monthly from the join date; the staff registers the payments.'
            : "The plan is billed monthly from the join date. Status follows the payments automatically; the plan's appointment allowance is counted per billing period (covered appointments of the holder and dependents)."
        }
      >
        {record && (
          <span className={`badge ${record.status}`}>{statusLabel(SUBSCRIPTION_STATUS, record.status)}</span>
        )}
        {record && !locked && session.canEditSubscriptions && !companyManaged && <button type="button" className="warn" onClick={cancelPlan}>Cancel plan</button>}
        {requesting && !pending && <button type="button" className="warn" onClick={requestCancellation}>Request cancellation</button>}
        {record && locked && session.isStaff && <button type="button" className="ghost" onClick={reactivate}>Reactivate</button>}
      </PageHeader>
      {pending && (
        <div className="notice warn" data-testid="pending-change">
          <strong>
            {pending.kind === 'cancel' ? 'Cancellation' : 'Change'} asked by {pending.seller_name} on {formatDate(pending.created_at)}, awaiting{' '}
            {session.isClient ? 'your' : "the customer's"} confirmation:
          </strong>{' '}
          {pending.summary}
          {pending.note && <> · reason: {pending.note}</>}
          {' '}— nothing changes until it is confirmed.
          <div className="actions" style={{ marginTop: 8 }}>
            {session.canDecideSaleChanges && (
              <>
                <button type="button" onClick={() => decideRequest('confirm')}>{session.isClient ? 'Confirm' : "Confirm (customer's answer)"}</button>
                <button type="button" className="ghost" onClick={() => decideRequest('reject')}>Reject</button>
              </>
            )}
            {requesting && <button type="button" className="ghost" onClick={withdrawRequest}>Withdraw my request</button>}
          </div>
        </div>
      )}
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          <h2>Plan</h2>
          <label>
            Customer *
            <select value={form.customer_id} required disabled={Boolean(id) || session.isClient} onChange={(event) => set('customer_id', event.target.value)}>
              <option value="">Select</option>
              {customers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            {session.isSeller && !id && (
              <small className="hint">Your customers only. <Link to="/customers/new">Register a new customer</Link> first if needed.</small>
            )}
          </label>
          {session.isSeller && region && !id && (
            <p className="notice info">
              Region {region.name}: {region.plans.length ? region.plans.map((item) => item.modality_name).join(', ') : 'no plans yet'} · scope {region.scope} · {region.commission_label} commission.
            </p>
          )}
          {session.canEnrollEmployees && (
            <label>
              Corporate agreement
              <select
                value={form.agreement_id}
                disabled={locked}
                onChange={(event) => {
                  set('agreement_id', event.target.value)
                  if (event.target.value) set('seller_id', '') // a corporate enrollment is never a seller's sale
                  // A new enrollment runs until the end of the six-month contract.
                  const chosen = agreements.find((item) => String(item.id) === event.target.value)
                  if (chosen && !id) {
                    const joinedOn = chosen.starts_on > isoDate(today()) ? chosen.starts_on : isoDate(today())
                    set('joined_on', joinedOn)
                    set('expires_on', chosen.ends_on)
                  }
                }}
              >
                <option value="">None (individual plan)</option>
                {[...new Set(agreementOptions.map((item) => item.partner_name))].map((partner) => (
                  <optgroup key={partner} label={partner}>
                    {agreementOptions
                      .filter((item) => item.partner_name === partner)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name} · {headcountLabel(item.employees, item.min_employees)}
                          {['in_force', 'scheduled'].includes(item.status) ? '' : ` (${item.status})`}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
              {agreement ? (
                <small className="hint">
                  Employee of {agreement.partner_name}: {agreement.plans.map((item) => `${item.modality_name} ${item.discount_percent}% off`).join(', ')}{' '}
                  · minimum {agreement.min_employees} employees · {PAYER[agreement.payer].toLowerCase()} · six-month contract {formatDate(agreement.starts_on)} –{' '}
                  {formatDate(agreement.ends_on)}.
                </small>
              ) : (
                <small className="hint">Enroll an employee of a partner company under its negotiated conditions.</small>
              )}
            </label>
          )}
          {session.canRecordSellerSales && (
            <label>
              Seller
              <select value={form.seller_id} disabled={locked || Boolean(form.agreement_id)} onChange={(event) => set('seller_id', event.target.value)}>
                <option value="">No seller (direct sale)</option>
                {regions.map((area) => {
                  const team = sellerOptions.filter((item) => item.region_id === area.id)
                  if (team.length === 0) return null
                  return (
                    <optgroup key={area.id} label={area.name}>
                      {team.map((item) => (
                        <option key={item.id} value={item.id}>{item.name}{item.active ? '' : ' (inactive)'}</option>
                      ))}
                    </optgroup>
                  )
                })}
              </select>
              {region ? (
                <small className="hint">
                  Region {region.name}: sells only to customers in {region.scope}, only{' '}
                  {region.plans.length ? region.plans.map((item) => item.modality_name).join(', ') : 'no plans yet'}; {region.commission_label} commission.
                </small>
              ) : (
                <small className="hint">A seller's sale follows the rules of the seller's region.</small>
              )}
            </label>
          )}
          <label>
            Plan *
            <select value={form.modality_id} required disabled={locked || readOnly} onChange={(event) => set('modality_id', event.target.value)}>
              <option value="">Select</option>
              {modalities.map((item) => {
                const corporate = agreementOffer(item.id)
                const regional = agreement ? null : offer(item.id)
                const current = record && String(record.modality_id) === String(item.id) && !saleChanged
                const outside = !agreement && region && !regional && !current
                const uncovered = agreement && !corporate
                // Enterprise plans: only through a company agreement (clients and sellers never see them).
                const enterpriseOnly = item.enterprise && !agreement && !current
                if (enterpriseOnly && (session.isClient || session.isSeller)) return null
                return (
                  <option key={item.id} value={item.id} disabled={Boolean(outside || uncovered || enterpriseOnly)}>
                    {item.name}
                    {regional && regional.discount_percent > 0 ? ` — ${regional.discount_percent}% off in ${region.name}` : ''}
                    {outside ? ` — not sold in ${region.name}` : ''}
                    {corporate ? ` — ${corporate.discount_percent}% off in ${agreement.name}` : ''}
                    {uncovered ? ` — not covered by ${agreement.name}` : ''}
                    {enterpriseOnly ? ' — enterprise plan: company agreements only' : ''}
                  </option>
                )
              })}
            </select>
            {plan && (
              <small className="hint">
                {money(plan.plan_value_cents)}/month · {plan.dependent_limit} dependents included
                {plan.extra_dependent_cents > 0 && `, ${money(plan.extra_dependent_cents)} each extra`}
                {plan.effective_grace_days > 0 ? ` · ${plan.effective_grace_days}-day waiting period` : ' · no waiting period'}
                {plan.category_name && ` · ${plan.category_name}${plan.enterprise ? ' (enterprise)' : ''}`}
                {' · '}{allowanceLabel(plan.appointment_limit, plan.appointment_limit_period).toLowerCase()} with {plan.discount_percent}% off
                {plan.appointment_limit > 0 && ', full price over that'}
              </small>
            )}
          </label>
          <label>
            Joined on *
            <input
              type="date"
              value={form.joined_on}
              required
              disabled={locked || readOnly}
              onChange={(event) => {
                set('joined_on', event.target.value)
                if (!id && event.target.value) set('expires_on', defaultExpiration(event.target.value))
              }}
            />
          </label>
          <label>
            Expires on *
            <input type="date" value={form.expires_on} required disabled={locked || readOnly} min={form.joined_on} onChange={(event) => set('expires_on', event.target.value)} />
            <small className="hint">Appointments are only allowed up to this date.</small>
          </label>
          {plan && form.customer_id && (
            <p className="notice info">
              {dependents} dependent{dependents === 1 ? '' : 's'}{extra > 0 && ` (${extra} above the limit)`} · monthly amount{' '}
              {!saleChanged && record ? (
                <>
                  {money(record.monthly_amount_cents)}
                  {record.agreement_id
                    ? record.monthly_amount_cents < record.list_amount_cents && ` (${record.discount_percent}% off ${money(record.list_amount_cents)}, agreement ${record.agreement_name})`
                    : record.discount_percent > 0 && ` (${record.discount_percent}% off ${money(record.list_amount_cents)}, as sold)`}
                </>
              ) : quote && quote.allowed && quote.discount_percent > 0 ? (
                <>{money(quote.monthly_amount_cents)} ({quote.discount_percent}% off {money(quote.list_amount_cents)})</>
              ) : (
                money(quote ? quote.list_amount_cents : monthly)
              )}
              {saleChanged && quote && quote.allowed && quote.seller_id && (
                <> · seller commission {quote.commission_bp / 100}% ({money(quote.commission_cents)} a month paid in full)</>
              )}
            </p>
          )}
          {quote && quote.agreement_id && quote.allowed && saleChanged && (
            <p className={`notice ${quote.conditions_met ? 'ok' : 'warn'}`} data-testid="agreement-quote">
              {quote.partner_name} · {quote.agreement_name}: the negotiated {quote.negotiated_discount_percent}% off applies
              {quote.grace_days === 0 ? ', no waiting period' : ''} — {PAYER[quote.payer].toLowerCase()}, until {formatDate(quote.agreement_ends_on)}.{' '}
              {headcountLabel(quote.employees, quote.min_employees)} in {monthLabel(form.joined_on.slice(0, 7))} with this enrollment
              {quote.conditions_met
                ? ': the minimum is met.'
                : `: ${quote.min_employees - quote.employees} short — the company pays the month's discounts back unless the minimum is reached.`}
              {quote.payer === 'shared' && (
                <>
                  {' '}Co-payment by default: the company {quote.company_share_percent}% ({money(quote.company_monthly_cents)}), the employee{' '}
                  {100 - quote.company_share_percent}% ({money(quote.employee_monthly_cents)}) a month.
                </>
              )}
            </p>
          )}
          {!id && agreement && agreement.payer === 'shared' && session.canEnrollEmployees && (
            <label>
              Company's part agreed with the employee (%)
              <input
                type="number"
                min={0}
                max={100}
                value={form.company_share_percent}
                placeholder={quote && quote.company_share_percent != null ? String(quote.company_share_percent) : ''}
                onChange={(event) => set('company_share_percent', event.target.value)}
              />
              <small className="hint">Blank: the contract's default for the plan. It may change later, from a date on.</small>
            </label>
          )}
          {blocked && <p className="notice warn">{quote.reason}</p>}
          {upgrading && (
            <div className="notice warn" data-testid="upgrade-notice">
              {current.customer_name} already has <strong>{current.modality_name}</strong> ({statusLabel(SUBSCRIPTION_STATUS, current.status)},{' '}
              {formatDate(current.joined_on)} – {formatDate(current.expires_on)}). A new plan <strong>upgrades</strong> it: the current plan ends on the day before
              the join date; its installments after that are no longer owed, the ones already paid stay.
              <label className="check" style={{ marginTop: 8 }}>
                <input type="checkbox" checked={form.upgrade} onChange={(event) => set('upgrade', event.target.checked)} />
                Upgrade the current plan
              </label>
              {form.upgrade && form.joined_on && form.joined_on <= current.joined_on && (
                <small className="hint">The new plan must start after {formatDate(current.joined_on)}, the current plan's join date.</small>
              )}
              {upgradeManaged && (
                <small className="hint">This plan is part of {current.partner_name}'s corporate agreement: changes go through your company (contact the staff).</small>
              )}
            </div>
          )}
          {companyManaged && (
            <p className="notice info">
              Corporate plan of {record.partner_name}: changes and cancellation go through your company (contact the staff).
            </p>
          )}
          {error && <p className="error">{error}</p>}
          <div className="actions">
            {!locked && !readOnly && (
              <button type="submit" disabled={saving || Boolean(blocked) || Boolean(upgradeBlocked)}>
                {id ? (requesting ? 'Request change' : 'Save') : upgrading ? 'Upgrade plan' : session.isSeller ? 'Record sale' : 'Create'}
              </button>
            )}
            {requesting && !pending && <small className="hint">The customer must confirm a change before it applies.</small>}
            <Link className="button ghost" to="/subscriptions">Back</Link>
          </div>
        </form>
        <div className="stack">
          {record && (
            <div className="card padded status-panel">
              <h2>Status</h2>
              <dl>
                <dt>Status</dt>
                <dd><span className={`badge ${record.status}`}>{statusLabel(SUBSCRIPTION_STATUS, record.status)}</span></dd>
                <dt>Usable from</dt>
                <dd>{formatDate(record.usable_from)}</dd>
                {(record.upgraded_from_id || record.upgraded_to_id) && (
                  <>
                    <dt>Upgrade</dt>
                    <dd>
                      {record.upgraded_from_id && <>Replaced <Link to={`/subscriptions/${record.upgraded_from_id}`}>the previous plan</Link></>}
                      {record.upgraded_from_id && record.upgraded_to_id && ' · '}
                      {record.upgraded_to_id && <>Replaced by <Link to={`/subscriptions/${record.upgraded_to_id}`}>a new plan</Link> (ended {formatDate(record.expires_on)})</>}
                    </dd>
                  </>
                )}
                <dt>{record.agreement_id ? 'Corporate plan' : 'Sold by'}</dt>
                <dd>
                  {record.agreement_id ? (
                    <>
                      {session.canSeePartners ? (
                        <Link to={`/agreements/${record.agreement_id}`}>{record.partner_name} · {record.agreement_name}</Link>
                      ) : (
                        `${record.partner_name} · ${record.agreement_name}`
                      )}
                      <br />
                      <small className="muted">
                        {[
                          payerLabel(record.payer, record.company_share_percent),
                          `${record.discount_percent}% off (agreement terms)`,
                          // The company's headcount is internal: the API leaves it null for clients.
                          record.agreement_employees != null
                            ? `${headcountLabel(record.agreement_employees, record.agreement_min_employees)} this month`
                            : '',
                        ].filter(Boolean).join(' · ')}
                      </small>
                      {record.payer === 'shared' && (
                        <div data-testid="split-panel">
                          <small>
                            Each month: the company <strong>{money(record.company_monthly_cents)}</strong>, {session.isClient ? 'you' : 'the employee'}{' '}
                            <strong>{money(record.employee_monthly_cents)}</strong>
                            {record.share_history.length > 1 && (
                              <> · splits: {record.share_history.map((row) => `${row.company_share_percent}% from ${formatDate(row.effective_from)}`).join(', ')}</>
                            )}
                          </small>
                          {session.isStaff && !locked && (
                            <div><button type="button" className="link" onClick={changeShare}>Change split</button></div>
                          )}
                        </div>
                      )}
                    </>
                  ) : record.seller_id ? (
                    <>
                      {record.seller_name} <small className="muted">· {record.region_name}</small>
                      <br />
                      <small className="muted">
                        {[
                          record.discount_percent > 0 ? `${record.discount_percent}% special discount` : '',
                          // The commission is internal: the API leaves it null for clients.
                          record.commission_bp != null ? `${record.commission_bp / 100}% commission · ${money(record.commission_earned_cents)} earned so far` : '',
                        ].filter(Boolean).join(' · ')}
                      </small>
                      {record.overrides.length > 0 && (
                        <>
                          <br />
                          <small className="muted">
                            Team overrides: {record.overrides.map((item) => `${item.seller_name} ${item.rate_bp / 100}% (level ${item.level}) · ${money(item.earned_cents)}`).join(' · ')}
                          </small>
                        </>
                      )}
                    </>
                  ) : (
                    <span className="muted">Direct sale (no seller)</span>
                  )}
                </dd>
                <dt>Period</dt>
                <dd>{formatDate(record.joined_on)} – {formatDate(record.expires_on)}</dd>
                {record.cancelled_on && (<><dt>Cancelled on</dt><dd>{formatDate(record.cancelled_on)}</dd></>)}
                <dt>Payments</dt>
                <dd>
                  {record.installments_paid}/{record.installments_total} paid
                  {record.installments_overdue > 0 && ` · ${record.installments_overdue} overdue`}
                  {record.next_due_on && ` · next due ${formatDate(record.next_due_on)}`}
                </dd>
                <dt>Appointments</dt>
                <dd>
                  {allowanceLabel(record.appointment_limit, record.appointment_limit_period)}
                  {record.usage_window_start && (
                    <>
                      {' · '}
                      {record.appointment_limit > 0 ? (
                        <span className={`badge ${record.appointments_remaining > 0 ? 'covered' : 'over-limit'}`}>
                          {record.appointments_used} of {record.appointment_limit} used
                        </span>
                      ) : (
                        `${record.appointments_used} booked`
                      )}
                      <small className="muted"> in {formatDate(record.usage_window_start)} – {formatDate(record.usage_window_end)}</small>
                    </>
                  )}
                  {record.appointment_limit > 0 && record.appointments_remaining === 0 && record.usage_window_start && (
                    <small className="muted"> · new appointments in this period pay the full price</small>
                  )}
                </dd>
              </dl>
            </div>
          )}
          {record && (
            <div className="card">
              <div className="card-head"><h2>Monthly installments</h2></div>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Period</th>
                    <th>Due</th>
                    <th>Amount</th>
                    {split ? (
                      <>
                        <th>Company part</th>
                        <th>{session.isClient ? 'Your part' : 'Employee part'}</th>
                      </>
                    ) : (
                      <th>Paid</th>
                    )}
                    <th>Status</th>
                    <th>Appointments</th>
                    {showCommission && <th>Commission</th>}
                    {session.canRegisterPayments && <th></th>}
                  </tr>
                </thead>
                <tbody>
                  {record.installments.map((item) => (
                    <tr key={item.id}>
                      <td>{item.sequence}</td>
                      <td>{formatDate(item.period_start)} – {formatDate(item.period_end)}</td>
                      <td>{formatDate(item.due_on)}</td>
                      <td>
                        {money(item.amount_cents)}
                        {item.discount_cents > 0 && <small className="muted"> · {money(item.discount_cents)} off</small>}
                      </td>
                      {split ? (
                        ['company', 'employee'].map((part) => {
                          const cents = part === 'company' ? item.company_share_cents : item.employee_share_cents
                          const paidOn = part === 'company' ? item.company_paid_on : item.employee_paid_on
                          return (
                            <td key={part}>
                              <Part cents={cents} paidOn={paidOn} />
                              {session.canRegisterPayments && cents > 0 && !locked && (
                                <>
                                  {' '}
                                  {paidOn ? (
                                    <button type="button" className="link" onClick={() => undoPart(item, part)}>undo</button>
                                  ) : (
                                    <button type="button" className="link" onClick={() => payPart(item, part)}>register</button>
                                  )}
                                </>
                              )}
                            </td>
                          )
                        })
                      ) : (
                        <td>{item.paid_on ? `${formatDate(item.paid_on)} · ${money(item.paid_cents)}` : '—'}</td>
                      )}
                      <td><span className={`badge ${item.status}`}>{statusLabel(INSTALLMENT_STATUS, item.status)}</span></td>
                      <td>
                        {record.appointment_limit > 0 && record.appointment_limit_period === 'month'
                          ? <span className={`badge ${item.appointments_used >= record.appointment_limit ? 'over-limit' : 'covered'}`}>{item.appointments_used}/{record.appointment_limit}</span>
                          : item.appointments_used}
                      </td>
                      {showCommission && (
                        <td>{item.paid_on ? <strong>{money(item.commission_cents)}</strong> : <span className="muted">when paid</span>}</td>
                      )}
                      {session.canRegisterPayments && (
                      <td className="actions">
                        {!session.canRegisterPayments ? null : item.paid_on ? (
                          <button type="button" className="ghost" onClick={() => undoPayment(item)}>Undo</button>
                        ) : (
                          !locked && (
                            <button
                              type="button"
                              className={nextOpen && nextOpen.id === item.id ? '' : 'ghost'}
                              onClick={() => pay(item)}
                            >
                              Register payment
                            </button>
                          )
                        )}
                      </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!record && <p className="muted">{session.isSeller ? 'Record the sale' : 'Create the subscription'} to generate the monthly installments.</p>}
          {record && !session.canRegisterPayments && (
            <p className="notice info">
              {session.isSeller
                ? 'Payments are registered by the staff; your commission on each one is shown as it is paid.'
                : record.paid_by_company
                  ? `Your fees are paid by ${record.partner_name} (one invoice a month for its employees).`
                  : record.payer === 'shared'
                    ? `Co-payment: ${record.partner_name} pays its part of each fee, you pay yours. Follow them in My payments.`
                  : 'Payments are registered by the clinic staff. Contact us after paying an installment.'}
            </p>
          )}
        </div>
      </div>
    </section>
  )
}
