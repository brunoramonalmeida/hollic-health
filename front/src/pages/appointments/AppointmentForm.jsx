import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import WeekCalendar from '../../components/WeekCalendar'
import AttendancePanel from '../../components/Attendance'
import SignaturePanel from '../../components/Signature'
import StartPanel from '../../components/StartPanel'
import PrescriptionsPanel from '../../components/Prescriptions'
import ChatPanel from '../../components/Chat'
import LocationMap from '../../components/LocationMap'
import { WEEKDAYS, formatDate, parseIsoDate, startOfWeek, today } from '../../dates'
import { APPOINTMENT_STATUS, SUBSCRIPTION_STATUS, allowanceLabel, capacityLabel, statusLabel } from '../../labels'
import { useSession } from '../../session'

const empty = { customer_id: '', dependent_id: '', professional_id: '', marked_at: '', status: 'agendada', notes: '', urgent: false, remote: false, meeting_url: '' }

/** How the plan's appointment allowance applies to this appointment (quote or stored snapshot). */
function allowanceNotice(quote) {
  const allowance = allowanceLabel(quote.plan_limit, quote.plan_limit_period)
  const window = quote.plan_window_start ? `${formatDate(quote.plan_window_start)} – ${formatDate(quote.plan_window_end)}` : ''
  if (quote.over_limit) {
    if (quote.snapshot) return `The plan covers ${allowance} and the allowance was already used when this was booked: it pays the full price.`
    return `The plan covers ${allowance}${window ? ` and the ${quote.plan_used} of ${window} are already booked` : ''}: this appointment pays the full price.`
  }
  if (!quote.plan_limit) return 'Unlimited appointments: every one gets the plan discount.'
  if (quote.snapshot) return `Covered by the plan (${allowance}).`
  return `${allowance} covered · ${quote.plan_used} already booked${window ? ` in ${window}` : ''} · ${quote.plan_remaining} left, including this one.`
}

/**
 * Moving an appointment to another day (or re-activating it) keeps the fees
 * snapshotted when it was booked and only re-evaluates the plan side: apply
 * the quoted discount (or none, over the limit) to the stored gross amount.
 * (Moving between a regular and an on-call slot changes the price list, so
 * the live quote is used as is.)
 */
function rebookedQuote(original, quoted) {
  if (
    Boolean(original.on_call) !== Boolean(quoted.on_call) ||
    Boolean(original.urgent) !== Boolean(quoted.urgent) ||
    Boolean(original.remote) !== Boolean(quoted.remote)
  ) {
    return { ...quoted, snapshot: false }
  }
  const gross = original.gross_cents
  const discount = Math.floor((gross * quoted.discount_percent + 50) / 100)
  return {
    ...quoted,
    fee_cents: original.fee_cents,
    specialty_price_cents: original.specialty_price_cents,
    professional_price_cents: original.professional_price_cents,
    gross_cents: gross,
    discount_cents: discount,
    amount_cents: gross - discount,
    taxes: [],
    tax_cents: 0,
    snapshot: false,
  }
}

export default function AppointmentForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const session = useSession()
  const [form, setForm] = useState(empty)
  const [original, setOriginal] = useState(null)
  const [quote, setQuote] = useState(null)
  const [customers, setCustomers] = useState([])
  const [dependents, setDependents] = useState([])
  const [plan, setPlan] = useState(undefined) // undefined: not loaded; null: no subscription
  const [professionals, setProfessionals] = useState([])
  const [clinics, setClinics] = useState([])
  const [weekStart, setWeekStart] = useState(() => startOfWeek(today()))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    const requests = [api('/customers'), api('/professionals'), api('/clinics')]
    if (id) requests.push(api(`/appointments/${id}`))
    Promise.all(requests)
      .then(([people, staff, places, row]) => {
        setCustomers(people)
        setProfessionals(staff)
        setClinics(places)
        if (row) {
          setOriginal(row)
          setForm({
            customer_id: String(row.customer_id),
            dependent_id: row.dependent_id ? String(row.dependent_id) : '',
            professional_id: String(row.professional_id),
            marked_at: row.marked_at.slice(0, 16),
            status: row.status,
            notes: row.notes || '',
            urgent: Boolean(row.urgent),
            remote: Boolean(row.remote),
            meeting_url: row.meeting_url || '',
          })
          setWeekStart(startOfWeek(parseIsoDate(row.marked_at.slice(0, 10))))
        } else {
          setOriginal(null)
          // A client always books for its own customer record.
          setForm({ ...empty, customer_id: session.isClient ? String(session.customerId) : '' })
        }
      })
      .catch((err) => setError(err.message))
  }, [id])

  useEffect(() => {
    if (!form.customer_id) {
      setDependents([])
      setPlan(undefined)
      return
    }
    Promise.all([api(`/customers/${form.customer_id}/dependents`), api(`/subscriptions/customer/${form.customer_id}`)])
      .then(([deps, subscription]) => {
        setDependents(deps)
        setPlan(subscription)
      })
      .catch((err) => setError(err.message))
  }, [form.customer_id])

  const planNotice = useMemo(() => {
    if (!form.customer_id || plan === undefined) return null
    if (plan === null) {
      return { kind: 'warn', text: 'No plan subscription. The plan is mandatory to schedule.' }
    }
    const label = statusLabel(SUBSCRIPTION_STATUS, plan.status)
    const period = `${formatDate(plan.joined_on)} – ${formatDate(plan.expires_on)}`
    if (plan.status === 'ativa') {
      const allowance = plan.appointment_limit > 0
        ? ` · ${allowanceLabel(plan.appointment_limit, plan.appointment_limit_period)} covered`
        : ' · unlimited appointments'
      return { kind: 'ok', text: `${plan.modality_name} · ${label} · usable from ${formatDate(plan.usable_from)} · valid until ${formatDate(plan.expires_on)}${allowance}` }
    }
    return { kind: 'warn', text: `${plan.modality_name} · ${label} (${period}). Scheduling is blocked until the plan is active.` }
  }, [form.customer_id, plan])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const professional = useMemo(
    () => professionals.find((item) => String(item.id) === form.professional_id) || null,
    [professionals, form.professional_id],
  )
  const clinic = useMemo(
    () => (professional ? clinics.find((item) => item.id === professional.clinic_id) || null : null),
    [clinics, professional],
  )

  // Amounts: the stored snapshot while customer, professional and slot are
  // unchanged, otherwise a live quote from the API (price list — the on-call
  // one in an on-call shift — + the customer's plan discount, if the plan's
  // allowance for that period allows).
  const quoteAt = form.marked_at ? form.marked_at.slice(0, 16) : ''
  const unchanged =
    original &&
    String(original.professional_id) === form.professional_id &&
    String(original.customer_id) === form.customer_id &&
    (!quoteAt || original.marked_at.slice(0, 16) === quoteAt) &&
    Boolean(original.urgent) === form.urgent &&
    Boolean(original.remote) === form.remote &&
    (form.status !== 'agendada' || original.status !== 'cancelada')

  useEffect(() => {
    if (unchanged) {
      setQuote({
        fee_cents: original.fee_cents,
        specialty_price_cents: original.specialty_price_cents,
        professional_price_cents: original.professional_price_cents,
        gross_cents: original.gross_cents,
        discount_percent: original.discount_percent,
        discount_cents: original.discount_cents,
        amount_cents: original.amount_cents,
        modality_name: original.modality_name,
        tax_cents: original.tax_cents,
        taxes: original.taxes,
        over_limit: original.over_limit,
        plan_limit: original.plan_limit,
        plan_limit_period: original.plan_limit_period,
        on_call: original.on_call,
        urgent: original.urgent,
        remote: original.remote,
        snapshot: true,
      })
      return undefined
    }
    if (!form.customer_id || !form.professional_id) {
      setQuote(null)
      return undefined
    }
    let active = true
    // The slot decides the price list (regular or on-call) and the period of
    // the plan's allowance; when editing, the appointment itself does not
    // count against that allowance.
    const params = new URLSearchParams({ customer_id: form.customer_id, professional_id: form.professional_id })
    if (quoteAt) params.set('at', quoteAt)
    if (form.urgent) params.set('urgent', 'true')
    if (form.remote) params.set('remote', 'true')
    if (original) params.set('appointment_id', String(original.id))
    const samePriceList =
      original && String(original.professional_id) === form.professional_id && String(original.customer_id) === form.customer_id
    api(`/appointments/quote?${params}`)
      .then((data) => active && setQuote(samePriceList ? rebookedQuote(original, data) : { ...data, snapshot: false }))
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [unchanged, original, form.customer_id, form.professional_id, quoteAt, form.urgent, form.remote])

  async function save(event) {
    event.preventDefault()
    setError('')
    if (!form.marked_at) {
      setError(form.urgent ? 'Inform the date and time of the urgent visit.' : 'Pick a slot in the agenda.')
      return
    }
    setSaving(true)
    try {
      await api(id ? `/appointments/${id}` : '/appointments', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Appointment updated' : 'Appointment scheduled',
        body: {
          customer_id: Number(form.customer_id),
          dependent_id: form.dependent_id ? Number(form.dependent_id) : null,
          professional_id: Number(form.professional_id),
          marked_at: form.marked_at,
          status: form.status,
          notes: form.notes,
          urgent: form.urgent,
          remote: form.remote,
          // Blank: the system issues a meeting room for a remote appointment.
          meeting_url: form.remote && form.meeting_url.trim() ? form.meeting_url.trim() : null,
        },
      })
      navigate('/appointments')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const selectedLabel = useMemo(() => {
    if (!form.marked_at) return ''
    const date = form.marked_at.slice(0, 10)
    const weekday = WEEKDAYS[(parseIsoDate(date).getDay() + 6) % 7]
    return `${weekday}, ${formatDate(date)} at ${form.marked_at.slice(11, 16)}`
  }, [form.marked_at])

  return (
    <section>
      <PageHeader
        back={{ to: '/appointments', label: 'Appointments' }}
        title={id ? 'Edit appointment' : 'New appointment'}
        hint="Choose the patient and the professional, then pick a free slot in the agenda. Days at the professional's or the clinic's capacity are shown as full and cannot be booked."
      />
      <form className="grid wide" onSubmit={save}>
        <div className="stack">
          <div className="panel">
            <h2>Patient</h2>
            <label>
              Customer *
              <select
                value={form.customer_id}
                required
                disabled={session.isClient}
                onChange={(event) => {
                  set('customer_id', event.target.value)
                  set('dependent_id', '')
                }}
              >
                <option value="">Select</option>
                {customers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            {planNotice && (
              <p className={`notice ${planNotice.kind}`}>
                {planNotice.text}{' '}
                {plan ? <Link to={`/subscriptions/${plan.id}`}>Open plan</Link> : <Link to="/subscriptions/new">Subscribe</Link>}
              </p>
            )}
            <label>
              Who is attending
              <select value={form.dependent_id} onChange={(event) => set('dependent_id', event.target.value)}>
                <option value="">The holder</option>
                {dependents.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}{item.kinship ? ` (${item.kinship})` : ''}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="panel">
            <h2>Professional</h2>
            <label>
              Professional *
              <select
                value={form.professional_id}
                required
                onChange={(event) => {
                  set('professional_id', event.target.value)
                  set('marked_at', '') // slots differ per professional
                }}
              >
                <option value="">Select</option>
                {professionals.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} — {item.specialty_name}</option>
                ))}
              </select>
            </label>
            {professional && (
              <p className="muted summary">
                {professional.specialty_name} · {professional.clinic_name} · {professional.slot_minutes}-minute slots
                {(professional.daily_limit > 0 || professional.monthly_limit > 0) &&
                  ` · capacity ${capacityLabel(professional.daily_limit, professional.monthly_limit)}`}
                {(professional.clinic_daily_limit > 0 || professional.clinic_monthly_limit > 0) &&
                  ` · clinic capacity ${capacityLabel(professional.clinic_daily_limit, professional.clinic_monthly_limit)}`}
                {professional.on_call_enabled && ` · on-call shifts at ${money(professional.on_call_price_cents)}`}
                {professional.urgent_enabled && ` · urgent visits at ${money(professional.urgent_price_cents)}`}
                {professional.remote_enabled && ` · remote appointments at ${money(professional.remote_fee_cents)}`}
              </p>
            )}
            {professional && professional.remote_enabled && (
              <>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.remote}
                    disabled={Boolean(original && original.signed_at) || form.urgent}
                    onChange={(event) => set('remote', event.target.checked)}
                  />
                  Remote appointment (video call)
                </label>
                <p className="muted summary">
                  {form.urgent
                    ? 'Urgent visits are attended in person.'
                    : 'Booked in a regular slot like an in-person one; not available in the on-call shifts. The patient and the professional get a link to join the call.'}
                </p>
              </>
            )}
            {professional && !professional.remote_enabled && (
              <p className="muted summary">This professional attends in person only.</p>
            )}
            {professional && professional.urgent_enabled && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.urgent}
                  disabled={Boolean(original && original.signed_at) || form.remote}
                  onChange={(event) => set('urgent', event.target.checked)}
                />
                Ad-hoc urgent visit
              </label>
            )}
            {form.urgent && (
              <label>
                Date and time of the visit *
                <input type="datetime-local" step={60} value={form.marked_at} required onChange={(event) => set('marked_at', event.target.value)} />
                <small className="hint">
                  Any time, inside or outside the working hours — except during the professional's on-call shifts, where urgent
                  visits are not accepted. The agenda on the right shows what is already taken.
                </small>
              </label>
            )}
            <label>
              Status
              <select value={form.status} onChange={(event) => set('status', event.target.value)}>
                {Object.entries(APPOINTMENT_STATUS).map(([value, label]) => (
                  // "Completed" is the professional's signature, never typed here;
                  // it is only offered when the appointment is already signed.
                  <option key={value} value={value} disabled={value === 'realizada' && !(original && original.signed_at)}>
                    {label}{value === 'realizada' ? ' (by signature)' : ''}
                  </option>
                ))}
              </select>
              <small className="hint">
                {original && original.signed_at
                  ? 'Signed by the professional: only the notes can change, or the appointment can be cancelled.'
                  : 'An appointment is completed by the professional\'s signature after the visit.'}
              </small>
            </label>
            <label>
              Notes
              <textarea value={form.notes} onChange={(event) => set('notes', event.target.value)} />
            </label>
          </div>
          <div className="panel quote">
            <h2>
              Amount {quote && quote.on_call && <span className="badge on-call">On-call service</span>}
              {quote && quote.urgent && <span className="badge urgent">Urgent visit</span>}
              {quote && quote.remote && <span className="badge remote">Remote appointment</span>}
            </h2>
            {quote ? (
              <dl>
                <dt>Clinic fee{quote.on_call ? ' (on-call)' : quote.remote ? ' (remote)' : ''}</dt><dd>{money(quote.fee_cents)}</dd>
                <dt>Specialty price{quote.on_call ? ' (on-call)' : quote.remote ? ' (remote)' : ''}</dt><dd>{money(quote.specialty_price_cents)}</dd>
                <dt className={quote.urgent ? 'urgent' : quote.on_call ? 'on-call' : quote.remote ? 'remote' : ''}>
                  Professional fee{quote.urgent ? ' (urgent rate)' : quote.on_call ? ' (on-call rate)' : quote.remote ? ' (remote rate)' : ''}
                </dt>
                <dd className={quote.urgent ? 'urgent' : quote.on_call ? 'on-call' : quote.remote ? 'remote' : ''}>{money(quote.professional_price_cents)}</dd>
                <dt>Gross</dt><dd>{money(quote.gross_cents)}</dd>
                {quote.over_limit ? (
                  <>
                    <dt className="over-limit">
                      Plan limit reached{quote.modality_name ? ` (${quote.modality_name})` : ''}
                    </dt>
                    <dd className="over-limit">no discount</dd>
                  </>
                ) : (
                  <>
                    <dt className="discount">
                      Plan discount {quote.discount_percent}%{quote.modality_name ? ` (${quote.modality_name})` : ''}
                    </dt>
                    <dd className="discount">− {money(quote.discount_cents)}</dd>
                  </>
                )}
                <dt className="total">Total</dt><dd className="total">{money(quote.amount_cents)}</dd>
                {session.isStaff && quote.taxes && quote.taxes.length > 0 && (
                  <>
                    <dt className="muted">Taxes included ({quote.taxes.map((line) => `${line.code} ${(line.rate_bp / 100).toFixed(2).replace(/\.?0+$/, '')}%`).join(', ')})</dt>
                    <dd className="muted">{money(quote.tax_cents)}</dd>
                  </>
                )}
              </dl>
            ) : (
              <p className="muted">Select the customer and the professional to see the amount.</p>
            )}
            {quote && quote.modality_name && (
              <p className={`notice ${quote.over_limit ? 'warn' : 'info'}`}>{allowanceNotice(quote)}</p>
            )}
            <small className="hint">
              {quote && quote.snapshot
                ? 'Price list and plan allowance at the time of scheduling. Changing the customer, the professional or the slot recalculates it.'
                : 'Price list (the on-call one in an on-call shift, the urgent rate for an urgent visit, the remote one for a video call) minus the discount of the customer\'s plan for this specialty — unless the plan\'s appointments for the period are used up — fixed when the appointment is saved.'}
            </small>
          </div>
          {original && (
            <AttendancePanel
              appointment={original}
              onChange={(updated) => {
                // Keep the form in step so a later Save does not revert the attendance notes.
                setOriginal(updated)
                setForm((current) => ({ ...current, status: updated.status, notes: updated.notes || '' }))
              }}
            />
          )}
          {original && (
            <StartPanel
              appointment={original}
              onChange={(updated) => {
                setOriginal(updated)
                setForm((current) => ({ ...current, status: updated.status, notes: updated.notes || '' }))
              }}
            />
          )}
          {original && <SignaturePanel appointment={original} />}
          {original && <PrescriptionsPanel appointment={original} />}
          {original && original.remote && session.isClient && <ChatPanel appointment={original} />}
          {original && original.remote && session.isStaff && (
            <div className="panel chat">
              <div className="card-head" style={{ padding: 0, marginBottom: 8 }}>
                <h2>Chat</h2>
                <span className="badge remote">🔒 end-to-end encrypted</span>
              </div>
              <p className="muted">
                The patient and the professional chat about this video call inside the system. Messages and attachments are encrypted in their
                browsers; the staff cannot read them.
              </p>
            </div>
          )}
        </div>
        <div className="stack">
          <div className="card padded">
            <div className="card-head">
              <h2>Agenda</h2>
              <span className={form.marked_at ? 'selected-slot' : 'muted'}>
                {form.marked_at ? `Selected: ${selectedLabel}` : 'No slot selected'}
              </span>
            </div>
            <WeekCalendar
              professionalId={form.professional_id ? Number(form.professional_id) : null}
              weekStart={weekStart}
              onWeekChange={setWeekStart}
              selected={form.marked_at}
              editingId={original ? original.id : null}
              remote={form.remote}
              onSelect={(date, time) => set('marked_at', `${date}T${time}`)}
            />
          </div>
          {form.remote && (
            <div className="card padded where remote-call">
              <div className="card-head">
                <h2>Video call</h2>
                <span className="badge remote">Remote appointment</span>
              </div>
              {original && original.remote && original.meeting_url ? (
                <p className="address">
                  <a href={original.meeting_url} target="_blank" rel="noreferrer">Join the call</a>
                  <br />
                  <small className="muted">{original.meeting_url}</small>
                </p>
              ) : (
                <p className="muted">A meeting room is issued when the appointment is saved; the patient and the professional receive the link.</p>
              )}
              {session.isStaff && (
                <label>
                  Meeting link
                  <input
                    type="url"
                    placeholder="Leave blank to issue a room automatically"
                    value={form.meeting_url}
                    onChange={(event) => set('meeting_url', event.target.value)}
                  />
                  <small className="hint">Optional: the professional's own room (Meet, Zoom, WhatsApp…). Blank keeps the current link or issues one.</small>
                </label>
              )}
              <p className="muted">
                The professional's clinic: {(original ? original.clinic_name : clinic?.name) || ''}
                {(original ? original.clinic_phone : clinic?.phone) ? ` · phone ${original ? original.clinic_phone : clinic.phone}` : ''}
              </p>
            </div>
          )}
          {!form.remote && (original || clinic) && (
            <div className="card padded where">
              <div className="card-head">
                <h2>Where to go</h2>
                <span className="muted">{(original ? original.clinic_name : clinic?.name) || ''}</span>
              </div>
              <p className="address">{original ? original.clinic_address : clinic?.address}</p>
              {(original ? original.clinic_phone : clinic?.phone) && <p className="muted">Phone: {original ? original.clinic_phone : clinic.phone}</p>}
              {(original ? original.clinic_directions : clinic?.directions) && (
                <p className="muted">How to get there: {original ? original.clinic_directions : clinic.directions}</p>
              )}
              <LocationMap
                latitude={original ? original.clinic_latitude : clinic?.latitude}
                longitude={original ? original.clinic_longitude : clinic?.longitude}
                address={original ? original.clinic_address : clinic?.address}
                height={220}
              />
            </div>
          )}
          {error && <p className="error">{error}</p>}
          <div className="actions">
            <button type="submit" disabled={saving}>{id ? 'Save' : 'Schedule'}</button>
            <Link className="button ghost" to="/appointments">Cancel</Link>
          </div>
        </div>
      </form>
    </section>
  )
}
