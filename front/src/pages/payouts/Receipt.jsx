import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { ConfirmationBadge, METHOD_LABEL, downloadReceipt } from '../../components/PayoutAccount'
import SignatureDialog from '../../components/SignatureDialog'
import { formatDate, formatDateTime } from '../../dates'
import { useSession } from '../../session'
import { toast } from '../../toast'

/** The digital receipt of an advance or an installment; the professional signs it here. */
export default function Receipt() {
  const { id } = useParams()
  const session = useSession()
  const [receipt, setReceipt] = useState(null)
  const [ack, setAck] = useState({ received_by: '', received_by_document: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [signing, setSigning] = useState(false)

  useEffect(() => {
    api(`/payouts/receipts/${id}`).then(setReceipt).catch((err) => setError(err.message))
  }, [id])

  async function sign(body) {
    setReceipt(await api(`/payouts/receipts/${id}/sign`, { method: 'POST', body, silent: true })) // errors stay in the dialog
    toast.success('Receipt signed — thank you')
    setSigning(false)
  }

  async function acknowledge(event) {
    event.preventDefault()
    setBusy(true)
    try {
      setReceipt(await api(`/payouts/receipts/${id}/acknowledge`, { method: 'POST', body: ack, message: 'Receipt acknowledged' }))
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false)
    }
  }

  if (error) return <p className="error">{error}</p>
  if (!receipt) return <p className="muted">Loading…</p>
  const payment = receipt.payment
  const back = session.isProfessional ? { to: '/my-payouts', label: 'My payouts' } : { to: `/payouts?payee=${payment.payee.payee_type}:${payment.payee.payee_id}`, label: 'Payouts' }

  return (
    <section>
      <PageHeader back={back} title={`${receipt.title} ${payment.receipt_number}`} hint={receipt.description}>
        <ConfirmationBadge payment={payment} />
        <button type="button" className="ghost" onClick={() => downloadReceipt(payment)}>Download PDF</button>
      </PageHeader>
      <div className="grid two">
        <div className="panel receipt" data-testid="receipt">
          <p className="muted">{receipt.company_name}{receipt.company_cnpj ? ` · CNPJ ${receipt.company_cnpj}` : ''}{receipt.company_address ? ` · ${receipt.company_address}` : ''}</p>
          <div className="status-panel"><dl>
            <dt>Paid to</dt><dd>{payment.payee.name} <small className="muted">({payment.payee.detail})</small></dd>
            <dt>Amount</dt><dd><strong data-testid="receipt-amount">{money(payment.amount_cents)}</strong></dd>
            <dt>Date</dt><dd>{formatDate(payment.paid_on)}</dd>
            <dt>Method</dt><dd>{METHOD_LABEL[payment.method] || payment.method}{payment.reference ? ` · ${payment.reference}` : ''}</dd>
            <dt>Registered by</dt><dd>{payment.registered_by_name || '—'}{payment.registered_at ? ` on ${formatDateTime(payment.registered_at)}` : ''}</dd>
            {payment.notes && <><dt>Notes</dt><dd>{payment.notes}</dd></>}
            <dt>Fingerprint</dt><dd><code>{receipt.fingerprint}</code></dd>
          </dl></div>
          {receipt.settlement && (
            <>
              <h3 className="section-title">Settlement {receipt.settlement.id} · {formatDate(receipt.settlement.period_start)} – {formatDate(receipt.settlement.period_end)}</h3>
              <div className="status-panel"><dl>
                <dt>Signed appointments</dt><dd>{receipt.settlement.appointments_count}</dd>
                <dt>Earned</dt><dd>{money(receipt.settlement.earned_cents)}</dd>
                <dt>Advances deducted</dt><dd>{money(receipt.settlement.advances_cents)}</dd>
                <dt>Net</dt><dd><strong>{money(receipt.settlement.net_cents)}</strong> in {receipt.settlement.installments_count} installment(s)</dd>
              </dl></div>
            </>
          )}
        </div>
        <div className="panel signature" data-testid="receipt-confirmation">
          <h2>Confirmation of receipt</h2>
          {payment.signed_at ? (
            <>
              <p className="muted">Digitally signed by {payment.signed_by_name} on {formatDateTime(payment.signed_at)} (password, authenticator code and hand-drawn signature), confirming the amount was received.</p>
              {payment.signature_image && <img className="signature-image" src={payment.signature_image} alt={`Signature of ${payment.signed_by_name}`} data-testid="receipt-signature-image" />}
            </>
          ) : payment.acknowledged_at ? (
            <p className="muted">Received by {payment.received_by}{payment.received_by_document ? ` (${payment.received_by_document})` : ''}, recorded by {payment.acknowledged_by_name} on {formatDateTime(payment.acknowledged_at)}.</p>
          ) : (
            <p className="notice warn">Not confirmed yet: {payment.payee.payee_type === 'professional' ? 'the professional has not signed this receipt.' : 'the clinic has not acknowledged this receipt.'}</p>
          )}
          {payment.can_sign && (
            <div className="actions">
              <button type="button" disabled={busy} onClick={() => setSigning(true)}>Sign receipt — I received {money(payment.amount_cents)}</button>
            </div>
          )}
          {signing && (
            <SignatureDialog
              title={`Sign receipt ${payment.receipt_number}`}
              summary={`You confirm having received ${money(payment.amount_cents)} on ${formatDate(payment.paid_on)}.`}
              confirmLabel="Sign receipt"
              onConfirm={sign}
              onClose={() => setSigning(false)}
            />
          )}
          {payment.can_acknowledge && (
            <form onSubmit={acknowledge}>
              <label>
                Received by *
                <input value={ack.received_by} required onChange={(event) => setAck({ ...ack, received_by: event.target.value })} />
              </label>
              <label>
                Document
                <input value={ack.received_by_document} onChange={(event) => setAck({ ...ack, received_by_document: event.target.value })} />
              </label>
              <div className="actions"><button type="submit" disabled={busy}>Acknowledge receipt</button></div>
            </form>
          )}
          {payment.settlement_id && !session.isProfessional && (
            <p style={{ marginTop: 12 }}><Link to={`/payouts?payee=${payment.payee.payee_type}:${payment.payee.payee_id}`}>Open the payee's account</Link></p>
          )}
        </div>
      </div>
    </section>
  )
}
