import { useEffect, useRef, useState } from 'react'
import { api, apiUrl } from '../api'
import { formatDate, formatDateTime } from '../dates'
import { toast } from '../toast'

// Same rule as the API (which decides on the content, not the name): text documents only.
const ACCEPT = '.pdf,.txt,.rtf,.doc,.docx,.odt,application/pdf,text/plain,application/rtf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.oasis.opendocument.text'
const EXTENSIONS = ['pdf', 'txt', 'rtf', 'doc', 'docx', 'odt']

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** The system's verification report of a signature (also used by the public check page). */
export function SignatureReport({ report }) {
  return (
    <div className={`signature-report ${report.valid ? 'ok' : 'bad'}`} data-testid="signature-report">
      <p className={`notice ${report.valid ? 'ok' : 'warn'}`}>
        {report.valid ? 'Valid signature' : 'Invalid signature'}
        {report.signer_name ? ` · ${report.signer_name}` : ''}
        {report.signer_document ? ` · CPF ${report.signer_document}` : ''}
        {report.signing_time ? ` · signed ${formatDateTime(report.signing_time)}` : ''}
      </p>
      <ul className="checks">
        {report.checks.map((check, index) => (
          <li key={`${check.name}-${index}`} className={check.ok ? 'ok' : 'bad'}>
            <strong>{check.ok ? '✓' : '✗'} {check.name}</strong>
            {check.detail && <small className="muted"> {check.detail}</small>}
          </li>
        ))}
      </ul>
      <small className="muted">
        {report.standard}
        {report.algorithm ? ` · ${report.algorithm}` : ''}
        {report.policy_oid ? ` · policy ${report.policy_oid}` : ''}
        {report.certificate_issuer ? ` · issued by ${report.certificate_issuer}` : ''}
        {!report.trust_bundle_configured && ' · no ICP-Brasil trust bundle configured on the server'}
      </small>
    </div>
  )
}

/** The professional's signing dialog: its ICP-Brasil A1 certificate (.pfx) and the password. */
function SignDialog({ item, appointmentId, onSigned, onClose }) {
  const [file, setFile] = useState(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (!file) return
    setBusy(true)
    setError('')
    const form = new FormData()
    form.append('certificate', file)
    form.append('password', password)
    try {
      const signed = await api(`/appointments/${appointmentId}/prescriptions/${item.id}/sign`, { method: 'POST', body: form, silent: true })
      toast.success('Prescription signed — it is now valid for pharmacies')
      onSigned(signed)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="sign-prescription-title" data-testid="sign-prescription-dialog">
      <form className="dialog" onSubmit={submit}>
        <h2 id="sign-prescription-title">Sign prescription</h2>
        <p className="muted">
          <strong>{item.filename}</strong> will be signed with your ICP-Brasil certificate (A1 file, .pfx or .p12). The certificate must carry your
          CPF; its password is used for this signature only and nothing of it is kept by the system.
        </p>
        <label>
          Certificate file (.pfx / .p12)
          <input type="file" accept=".pfx,.p12,application/x-pkcs12" required onChange={(event) => setFile(event.target.files && event.target.files[0])} />
        </label>
        <label>
          Certificate password
          <input type="password" autoComplete="off" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={!file || busy}>{busy ? 'Signing…' : 'Sign'}</button>
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </div>
  )
}

/**
 * Medical prescriptions attached to an appointment: the list with download
 * links (signed, they expire within 30 days — reopening the page issues new
 * ones), the upload and the removal for the staff and the appointment's
 * professional, and the professional's digital signature (ICP-Brasil),
 * which is what makes a prescription valid for pharmacies. Patients only
 * see signed prescriptions.
 */
export default function PrescriptionsPanel({ appointment, onChange }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [signing, setSigning] = useState(null) // the item being signed
  const [reports, setReports] = useState({}) // item id -> verification report
  const input = useRef(null)

  function load() {
    return api(`/appointments/${appointment.id}/prescriptions`)
      .then((result) => {
        setData(result)
        setError('')
      })
      .catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
  }, [appointment.id, appointment.status])

  async function upload(event) {
    const file = event.target.files && event.target.files[0]
    if (!file) return
    setError('')
    const extension = file.name.split('.').pop().toLowerCase()
    if (!EXTENSIONS.includes(extension)) {
      setError(`"${file.name}" is not a text document. Accepted: ${data ? data.accepted : 'PDF and similar documents'}.`)
      event.target.value = ''
      return
    }
    if (data && file.size > data.max_bytes) {
      setError(`"${file.name}" exceeds the limit of ${size(data.max_bytes)}.`)
      event.target.value = ''
      return
    }
    const form = new FormData()
    form.append('file', file)
    if (note.trim()) form.append('note', note.trim())
    setBusy(true)
    try {
      await api(`/appointments/${appointment.id}/prescriptions`, { method: 'POST', body: form, message: 'Prescription attached — sign it to make it valid', silent: true })
      setNote('')
      await load()
      onChange?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
      event.target.value = ''
    }
  }

  async function remove(item) {
    if (!window.confirm(`Remove "${item.filename}"? Its download links stop working at once.`)) return
    setBusy(true)
    try {
      await api(`/appointments/${appointment.id}/prescriptions/${item.id}`, { method: 'DELETE', message: 'Prescription removed' })
      await load()
      onChange?.()
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false)
    }
  }

  async function verify(item) {
    try {
      const report = await api(`/appointments/${appointment.id}/prescriptions/${item.id}/signature`)
      setReports((current) => ({ ...current, [item.id]: report }))
    } catch {
      /* toast already shown */
    }
  }

  const items = data ? data.items : []

  return (
    <div className="panel prescriptions" data-testid="prescriptions-panel">
      <div className="card-head" style={{ padding: 0, marginBottom: 8 }}>
        <h2>Prescriptions</h2>
        <span className="muted">{data ? `${items.length} of ${data.max_files}` : ''}</span>
      </div>
      {items.length > 0 ? (
        <ul className="files">
          {items.map((item) => (
            <li key={item.id} className={item.signed ? 'signed' : 'draft'}>
              <div className="file-head">
                <a href={apiUrl(item.download_url)} target="_blank" rel="noreferrer" download={item.filename}>{item.filename}</a>
                {item.signed ? (
                  <span className="badge signed" title={`Signed by ${item.signer_name} (CPF ${item.signer_document}) on ${formatDateTime(item.signed_at)}`}>Signed · ICP-Brasil</span>
                ) : (
                  <span className="badge awaiting-signature" title="Not valid for pharmacies until the professional signs it">Not signed</span>
                )}
              </div>
              <small className="muted">
                {size(item.size_bytes)} · attached {formatDateTime(item.uploaded_at)}{item.uploaded_by_name ? ` by ${item.uploaded_by_name}` : ''}
                {item.note ? ` · ${item.note}` : ''}
                <br />
                {item.signed
                  ? <>Signed {formatDateTime(item.signed_at)} by {item.signer_name} · CPF {item.signer_document} · certificate by {item.certificate_issuer} · <a href={apiUrl(item.signature_url)} target="_blank" rel="noreferrer">signature (.p7s)</a><br /></>
                  : null}
                Link valid until {formatDateTime(item.link_expires_at)} · kept until {formatDate(item.retain_until)}
              </small>
              <div className="actions file-actions">
                {item.can_sign && (
                  <button type="button" disabled={busy || (data && !data.signing_available)} onClick={() => setSigning(item)} title={data && !data.signing_available ? 'The ICP-Brasil trust bundle is not configured on the server' : ''}>
                    Sign
                  </button>
                )}
                {item.signed && <button type="button" className="ghost" onClick={() => verify(item)}>Verify</button>}
                {item.can_delete && <button type="button" className="ghost" disabled={busy} onClick={() => remove(item)}>Remove</button>}
              </div>
              {reports[item.id] && <SignatureReport report={reports[item.id]} />}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          {appointment.status === 'cancelada'
            ? 'Cancelled appointment: no prescriptions are kept.'
            : data && data.can_attach
              ? 'No prescription attached.'
              : 'No signed prescription yet.'}
        </p>
      )}
      {data && data.can_attach && data.remaining > 0 && (
        <div className="upload">
          <label>
            Note (optional)
            <input value={note} maxLength={200} placeholder="e.g. Antibiotic, 7 days" onChange={(event) => setNote(event.target.value)} />
          </label>
          <input ref={input} type="file" accept={ACCEPT} style={{ display: 'none' }} onChange={upload} />
          <div className="actions">
            <button type="button" disabled={busy} onClick={() => input.current && input.current.click()}>
              {busy ? 'Sending…' : 'Attach prescription'}
            </button>
            <small className="muted">{data.accepted}; up to {size(data.max_bytes)}; {data.remaining} more file{data.remaining === 1 ? '' : 's'}.</small>
          </div>
        </div>
      )}
      {data && data.can_attach && data.remaining === 0 && <p className="muted">The limit of {data.max_files} files is reached; remove one to attach another.</p>}
      <small className="hint">
        A prescription is valid for pharmacies once the appointment's professional signs it with its ICP-Brasil certificate; the signature file
        (.p7s) is verifiable by the system and by ITI's validator. Download links are valid for {data ? data.link_days : 30} days; reopen the
        appointment for new ones. Prescriptions are deleted when the appointment is cancelled and kept for at most 10 years after the visit.
      </small>
      {error && <p className="error">{error}</p>}
      {signing && (
        <SignDialog
          item={signing}
          appointmentId={appointment.id}
          onClose={() => setSigning(null)}
          onSigned={async () => {
            setSigning(null)
            await load()
            onChange?.()
          }}
        />
      )}
    </div>
  )
}
