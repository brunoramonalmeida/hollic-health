import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money, toCents } from '../api'
import { formatDate, formatDateTime, isoDate, today } from '../dates'
import { useSession } from '../session'
import { toast } from '../toast'
import SignatureDialog from './SignatureDialog'

const API_URL = import.meta.env.VITE_API_URL || '/api'

export const METHOD_LABEL = { pix: 'PIX', transfer: 'Bank transfer', cash: 'Cash', check: 'Cheque', other: 'Other' }
export const SETTLEMENT_STATUS = { open: 'Open', awaiting_signature: 'Awaiting signature', settled: 'Settled', void: 'Void' }

/** Authenticated download of a receipt PDF (links cannot carry the bearer token). */
export async function downloadReceipt(payment) {
  const response = await fetch(`${API_URL}/payouts/receipts/${payment.id}.pdf`, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
  if (!response.ok) {
    toast.error('Could not download the receipt.')
    return
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `receipt_${payment.receipt_number}.pdf`
  anchor.click()
  URL.revokeObjectURL(url)
}

export function ConfirmationBadge({ payment }) {
  if (!payment.paid_on) return <span className="badge none">Not paid</span>
  if (payment.signed_at) return <span className="badge signed">Signed</span>
  if (payment.acknowledged_at) return <span className="badge signed">Acknowledged</span>
  return <span className="badge awaiting-signature">Awaiting signature</span>
}

function MethodFields({ form, set }) {
  return (
    <>
      <label>
        Paid on *
        <input type="date" value={form.paid_on} required onChange={(event) => set('paid_on', event.target.value)} />
      </label>
      <label>
        Method
        <select value={form.method} onChange={(event) => set('method', event.target.value)}>
          {Object.entries(METHOD_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        Reference
        <input value={form.reference} placeholder="Bank reference, cheque number…" onChange={(event) => set('reference', event.target.value)} />
      </label>
    </>
  )
}

/**
 * A payee's payout account: position, settlements (with installments),
 * advances and receipts. With `manage` (administrators) it also offers the
 * forms to register an advance, create a settlement, pay installments and
 * acknowledge clinic receipts. Professionals sign their receipts here.
 */
export default function PayoutAccount({ payeeType, payeeId, manage = false }) {
  const session = useSession()
  const [position, setPosition] = useState(null)
  const [settlements, setSettlements] = useState([])
  const [advances, setAdvances] = useState([])
  const [receipts, setReceipts] = useState([])
  const [error, setError] = useState('')
  const [open, setOpen] = useState(null) // settlement id expanded
  const [detail, setDetail] = useState(null) // expanded settlement with appointments
  const [paying, setPaying] = useState(null) // installment being paid
  const [advanceForm, setAdvanceForm] = useState(null)
  const [settlementForm, setSettlementForm] = useState(null)
  const [ackForm, setAckForm] = useState(null)
  const [signing, setSigning] = useState(null) // receipt being signed
  const [busy, setBusy] = useState(false)

  const query = `payee_type=${payeeType}&payee_id=${payeeId}`

  async function load() {
    setError('')
    try {
      const [pos, items, adv, rec] = await Promise.all([
        api(`/payouts/position?${query}`),
        api(`/payouts/settlements?${query}`),
        api(`/payouts/advances?${query}`),
        api(`/payouts/receipts?${query}`),
      ])
      setPosition(pos)
      setSettlements(items)
      setAdvances(adv)
      setReceipts(rec)
      if (open) setDetail(await api(`/payouts/settlements/${open}`))
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    setOpen(null)
    setDetail(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payeeType, payeeId])

  async function toggle(settlement) {
    if (open === settlement.id) {
      setOpen(null)
      setDetail(null)
      return
    }
    setOpen(settlement.id)
    setDetail(await api(`/payouts/settlements/${settlement.id}`).catch(() => null))
  }

  async function run(action, message) {
    setBusy(true)
    try {
      await action()
      if (message) toast.success(message)
      await load()
      return true
    } catch (err) {
      setError(err.message)
      return false
    } finally {
      setBusy(false)
    }
  }

  async function submitAdvance(event) {
    event.preventDefault()
    const ok = await run(() => api('/payouts/advances', {
      method: 'POST',
      message: 'Advance registered — receipt issued',
      body: { payee_type: payeeType, payee_id: payeeId, amount_cents: toCents(advanceForm.amount), paid_on: advanceForm.paid_on, method: advanceForm.method, reference: advanceForm.reference, notes: advanceForm.notes },
    }))
    if (ok) setAdvanceForm(null)
  }

  async function submitSettlement(event) {
    event.preventDefault()
    const ok = await run(() => api('/payouts/settlements', {
      method: 'POST',
      message: 'Settlement created',
      body: { payee_type: payeeType, payee_id: payeeId, until: settlementForm.until, installments: Number(settlementForm.installments), first_due_on: settlementForm.first_due_on, interval_days: Number(settlementForm.interval_days), notes: settlementForm.notes },
    }))
    if (ok) setSettlementForm(null)
  }

  async function submitPayment(event) {
    event.preventDefault()
    const ok = await run(() => api(`/payouts/settlements/${paying.settlement_id}/installments/${paying.id}`, {
      method: 'PUT',
      message: 'Installment paid — receipt issued',
      body: { paid_on: paying.paid_on, method: paying.method, reference: paying.reference, notes: '' },
    }))
    if (ok) setPaying(null)
  }

  async function submitAck(event) {
    event.preventDefault()
    const ok = await run(() => api(`/payouts/receipts/${ackForm.id}/acknowledge`, {
      method: 'POST',
      message: 'Receipt acknowledged',
      body: { received_by: ackForm.received_by, received_by_document: ackForm.received_by_document },
    }))
    if (ok) setAckForm(null)
  }

  async function signReceipt(body) {
    await api(`/payouts/receipts/${signing.id}/sign`, { method: 'POST', body, silent: true }) // errors stay in the dialog
    toast.success(`Receipt ${signing.receipt_number} signed`)
    setSigning(null)
    await load()
  }

  function voidSettlement(settlement) {
    if (!window.confirm('Void this settlement? Its appointments and advances go back to pending.')) return
    run(() => api(`/payouts/settlements/${settlement.id}/void`, { method: 'POST', message: 'Settlement voided' }))
  }

  function deleteAdvance(advance) {
    if (!window.confirm(`Remove the advance ${advance.receipt_number}?`)) return
    run(() => api(`/payouts/advances/${advance.id}`, { method: 'DELETE', message: 'Advance removed' }))
  }

  if (error && !position) return <p className="error">{error}</p>
  if (!position) return <p className="muted">Loading…</p>

  const isClinic = payeeType === 'clinic'
  const receiptActions = (payment) => (
    <span className="actions">
      <Link className="button ghost" to={`/receipts/${payment.id}`}>Receipt</Link>
      <button type="button" className="ghost" onClick={() => downloadReceipt(payment)}>PDF</button>
      {payment.can_sign && <button type="button" disabled={busy} onClick={() => setSigning(payment)}>Sign</button>}
      {manage && payment.can_acknowledge && (
        <button type="button" className="ghost" onClick={() => setAckForm({ id: payment.id, received_by: '', received_by_document: '' })}>Acknowledge</button>
      )}
    </span>
  )

  return (
    <div className="stack" data-testid="payout-account">
      {signing && (
        <SignatureDialog
          title={`Sign receipt ${signing.receipt_number}`}
          summary={`You confirm having received ${money(signing.amount_cents)} on ${formatDate(signing.paid_on)}.`}
          confirmLabel="Sign receipt"
          onConfirm={signReceipt}
          onClose={() => setSigning(null)}
        />
      )}
      {error && <p className="error">{error}</p>}
      <div className="kpis">
        <div className="kpi accent"><span>Balance to receive</span><strong data-testid="balance">{money(position.balance_cents)}</strong><small>earned + open installments − advances</small></div>
        <div className="kpi"><span>Signed, not settled</span><strong>{money(position.unsettled_cents)}</strong><small>{position.unsettled_appointments} appointments</small></div>
        <div className="kpi"><span>Advances outstanding</span><strong>{money(position.advances_outstanding_cents)}</strong><small>to deduct</small></div>
        <div className="kpi"><span>Installments to receive</span><strong>{money(position.open_installments_cents)}</strong><small>{position.open_installments} open</small></div>
        <div className="kpi" data-testid="receipts-to-confirm"><span>{isClinic ? 'Receipts to acknowledge' : 'Receipts to sign'}</span><strong>{position.receipts_to_confirm}</strong><small>{money(position.receipts_to_confirm_cents)}</small></div>
        <div className="kpi"><span>Received so far</span><strong>{money(position.paid_cents)}</strong><small>advances + installments</small></div>
      </div>

      {manage && (
        <div className="toolbar">
          <button type="button" onClick={() => setAdvanceForm({ amount: '', paid_on: isoDate(today()), method: 'pix', reference: '', notes: '' })}>Register advance</button>
          <button type="button" onClick={() => setSettlementForm({ until: isoDate(today()), installments: 1, first_due_on: isoDate(today()), interval_days: 30, notes: '' })}>Create settlement</button>
        </div>
      )}

      {advanceForm && (
        <form className="panel" onSubmit={submitAdvance} data-testid="advance-form">
          <h2>Register an advance</h2>
          <div className="fields">
            <label>
              Amount (R$) *
              <input value={advanceForm.amount} required inputMode="decimal" placeholder="0,00" onChange={(event) => setAdvanceForm({ ...advanceForm, amount: event.target.value })} />
            </label>
            <MethodFields form={advanceForm} set={(field, value) => setAdvanceForm({ ...advanceForm, [field]: value })} />
            <label className="span-4">
              Notes
              <input value={advanceForm.notes} onChange={(event) => setAdvanceForm({ ...advanceForm, notes: event.target.value })} />
            </label>
          </div>
          <p className="muted">The advance is deducted from the next settlement(s); a receipt is issued for the payee to confirm.</p>
          <div className="actions">
            <button type="submit" disabled={busy || toCents(advanceForm.amount) <= 0}>Register advance</button>
            <button type="button" className="ghost" onClick={() => setAdvanceForm(null)}>Cancel</button>
          </div>
        </form>
      )}

      {settlementForm && (
        <form className="panel" onSubmit={submitSettlement} data-testid="settlement-form">
          <h2>Create a settlement</h2>
          <div className="fields">
            <label>
              Signed appointments up to *
              <input type="date" value={settlementForm.until} required onChange={(event) => setSettlementForm({ ...settlementForm, until: event.target.value })} />
            </label>
            <label>
              Installments *
              <input type="number" min={1} max={12} value={settlementForm.installments} required onChange={(event) => setSettlementForm({ ...settlementForm, installments: event.target.value })} />
            </label>
            <label>
              First due on *
              <input type="date" value={settlementForm.first_due_on} required onChange={(event) => setSettlementForm({ ...settlementForm, first_due_on: event.target.value })} />
            </label>
            <label>
              Days between installments
              <input type="number" min={1} max={120} value={settlementForm.interval_days} onChange={(event) => setSettlementForm({ ...settlementForm, interval_days: event.target.value })} />
            </label>
            <label className="span-4">
              Notes
              <input value={settlementForm.notes} onChange={(event) => setSettlementForm({ ...settlementForm, notes: event.target.value })} />
            </label>
          </div>
          <p className="muted">
            Gathers every signed appointment not settled yet up to that date ({money(position.unsettled_cents)} pending today), deducts the outstanding advances ({money(position.advances_outstanding_cents)}) and splits the net into the installments.
          </p>
          <div className="actions">
            <button type="submit" disabled={busy}>Create settlement</button>
            <button type="button" className="ghost" onClick={() => setSettlementForm(null)}>Cancel</button>
          </div>
        </form>
      )}

      {paying && (
        <form className="panel" onSubmit={submitPayment} data-testid="payment-form">
          <h2>Pay installment {paying.sequence} of {paying.installments_count} · {money(paying.amount_cents)}</h2>
          <div className="fields">
            <MethodFields form={paying} set={(field, value) => setPaying({ ...paying, [field]: value })} />
          </div>
          <div className="actions">
            <button type="submit" disabled={busy}>Register payment</button>
            <button type="button" className="ghost" onClick={() => setPaying(null)}>Cancel</button>
          </div>
        </form>
      )}

      {ackForm && (
        <form className="panel" onSubmit={submitAck} data-testid="ack-form">
          <h2>Acknowledge the clinic's receipt</h2>
          <div className="fields">
            <label className="span-2">
              Received by *
              <input value={ackForm.received_by} required onChange={(event) => setAckForm({ ...ackForm, received_by: event.target.value })} />
            </label>
            <label className="span-2">
              Document
              <input value={ackForm.received_by_document} onChange={(event) => setAckForm({ ...ackForm, received_by_document: event.target.value })} />
            </label>
          </div>
          <div className="actions">
            <button type="submit" disabled={busy}>Acknowledge</button>
            <button type="button" className="ghost" onClick={() => setAckForm(null)}>Cancel</button>
          </div>
        </form>
      )}

      <div className="card" data-testid="settlements">
        <div className="card-head"><h2>Settlements</h2><span className="muted">{settlements.length} records</span></div>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Period</th>
              <th>Status</th>
              <th>Appointments</th>
              <th>Earned</th>
              <th>Advances</th>
              <th>Net</th>
              <th>Paid</th>
              <th>Installments</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {settlements.map((row) => (
              <tr key={row.id} className={open === row.id ? 'selected' : ''}>
                <td>{row.id}</td>
                <td>{formatDate(row.period_start)} – {formatDate(row.period_end)}</td>
                <td><span className={`badge settlement-${row.status}`}>{SETTLEMENT_STATUS[row.status] || row.status}</span></td>
                <td>{row.appointments_count}</td>
                <td>{money(row.earned_cents)}</td>
                <td>{row.advances_cents ? `− ${money(row.advances_cents)}` : '—'}</td>
                <td><strong>{money(row.net_cents)}</strong></td>
                <td>{money(row.paid_cents)}</td>
                <td>{row.installments.filter((i) => i.paid_on).length}/{row.installments_count}</td>
                <td className="actions">
                  <button type="button" className="ghost" onClick={() => toggle(row)}>{open === row.id ? 'Close' : 'Open'}</button>
                  {manage && row.can_void && <button type="button" className="warn" onClick={() => voidSettlement(row)}>Void</button>}
                </td>
              </tr>
            ))}
            {settlements.length === 0 && <tr><td colSpan={10} className="muted">No settlement yet.</td></tr>}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="card" data-testid="settlement-detail">
          <div className="card-head">
            <h2>Settlement {detail.id} · {formatDate(detail.period_start)} – {formatDate(detail.period_end)}</h2>
            <span className="muted">created by {detail.created_by_name || '—'} on {formatDateTime(detail.created_at)}{detail.notes ? ` · ${detail.notes}` : ''}</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Installment</th>
                <th>Due</th>
                <th>Amount</th>
                <th>Paid</th>
                <th>Method</th>
                <th>Receipt</th>
                <th>Confirmation</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {detail.installments.map((item) => (
                <tr key={item.id}>
                  <td>{item.sequence} of {detail.installments_count}</td>
                  <td>{formatDate(item.due_on)}</td>
                  <td><strong>{money(item.amount_cents)}</strong></td>
                  <td>{item.paid_on ? formatDate(item.paid_on) : <span className="muted">—</span>}</td>
                  <td>{item.paid_on ? `${METHOD_LABEL[item.method] || item.method}${item.reference ? ` · ${item.reference}` : ''}` : '—'}</td>
                  <td>{item.receipt_number || '—'}</td>
                  <td><ConfirmationBadge payment={item} /></td>
                  <td className="actions">
                    {item.paid_on ? receiptActions(item) : manage ? (
                      <button type="button" onClick={() => setPaying({ ...item, paid_on: isoDate(today()), method: 'transfer', reference: '' })}>Register payment</button>
                    ) : <span className="muted">not paid yet</span>}
                  </td>
                </tr>
              ))}
              {detail.installments.length === 0 && <tr><td colSpan={8} className="muted">Fully covered by advances: nothing to pay.</td></tr>}
              {detail.advances_applied.map((item) => (
                <tr key={`adv-${item.advance_id}`}>
                  <td className="muted">Advance {item.receipt_number || item.advance_id}</td>
                  <td className="muted">{item.paid_on ? formatDate(item.paid_on) : '—'}</td>
                  <td className="muted">− {money(item.amount_cents)}</td>
                  <td colSpan={5} className="muted">deducted from this settlement</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="card-head"><h2>Appointments in this settlement</h2><span className="muted">{detail.appointments.length}</span></div>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Patient</th>
                <th>Professional</th>
                <th>Clinic</th>
                <th>Fee</th>
              </tr>
            </thead>
            <tbody>
              {detail.appointments.map((row) => (
                <tr key={row.appointment_id}>
                  <td><Link to={`/appointments/${row.appointment_id}`}>{formatDateTime(row.marked_at)}</Link></td>
                  <td>{row.patient}</td>
                  <td>{row.professional_name}</td>
                  <td>{row.clinic_name}</td>
                  <td>{money(row.fee_cents)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="stack">
        <div className="card" data-testid="advances">
          <div className="card-head"><h2>Advances</h2><span className="muted">{advances.length} records</span></div>
          <table>
            <thead>
              <tr>
                <th>Receipt</th>
                <th>Paid on</th>
                <th>Amount</th>
                <th>Deducted</th>
                <th>Confirmation</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {advances.map((row) => (
                <tr key={row.id}>
                  <td>{row.receipt_number}</td>
                  <td>{formatDate(row.paid_on)}</td>
                  <td><strong>{money(row.amount_cents)}</strong></td>
                  <td>{money(row.applied_cents)}{row.outstanding_cents ? <small className="muted"> · {money(row.outstanding_cents)} to deduct</small> : null}</td>
                  <td><ConfirmationBadge payment={row} /></td>
                  <td className="actions">
                    {receiptActions(row)}
                    {manage && row.can_delete && <button type="button" className="warn" onClick={() => deleteAdvance(row)}>Remove</button>}
                  </td>
                </tr>
              ))}
              {advances.length === 0 && <tr><td colSpan={6} className="muted">No advance.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="card" data-testid="receipts">
          <div className="card-head"><h2>Receipts</h2><span className="muted">{receipts.length} payments</span></div>
          <table>
            <thead>
              <tr>
                <th>Receipt</th>
                <th>Kind</th>
                <th>Paid on</th>
                <th>Amount</th>
                <th>Confirmation</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {receipts.map((row) => (
                <tr key={row.id}>
                  <td>{row.receipt_number}</td>
                  <td>{row.kind === 'advance' ? 'Advance' : `Installment ${row.sequence}/${row.installments_count}`}</td>
                  <td>{formatDate(row.paid_on)}</td>
                  <td><strong>{money(row.amount_cents)}</strong></td>
                  <td><ConfirmationBadge payment={row} /></td>
                  <td className="actions">{receiptActions(row)}</td>
                </tr>
              ))}
              {receipts.length === 0 && <tr><td colSpan={6} className="muted">No payment yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
