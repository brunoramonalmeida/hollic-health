import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { SignatureReport } from '../components/Prescriptions'

/**
 * Public check for pharmacies (no login): the prescription file and its
 * detached signature (.p7s) are verified by the system — the signature,
 * the chain up to the ICP-Brasil roots, the validity — and the signer is
 * matched against the network's professionals. Nothing is stored.
 */
export default function VerifyPrescription() {
  const [file, setFile] = useState(null)
  const [signature, setSignature] = useState(null)
  const [report, setReport] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault()
    if (!file || !signature) return
    setBusy(true)
    setError('')
    setReport(null)
    const form = new FormData()
    form.append('file', file)
    form.append('signature', signature)
    try {
      setReport(await api('/prescriptions/verify', { method: 'POST', body: form, silent: true, message: false }))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <section className="login-brand">
        <div className="brand-block">
          <span className="brand-mark">S</span>
          <p className="brand-name">Santé Saúde<small>Prescriptions</small></p>
        </div>
        <h2>Prescription check</h2>
        <p>For pharmacies: verify the digital signature of a prescription issued in the Santé Saúde network.</p>
      </section>
      <section className="login-panel verify-page">
        <h1>Verify a prescription</h1>
        <p className="hint">
          Pharmacies: upload the prescription file and its signature file (.p7s) handed by the patient. The system checks the digital
          signature (ICP-Brasil, CAdES) and tells whether the signer is a professional of the Santé Saúde network.
        </p>
        <form onSubmit={submit} className="stack">
          <label>
            Prescription file
            <input type="file" required onChange={(event) => setFile(event.target.files && event.target.files[0])} />
          </label>
          <label>
            Signature file (.p7s)
            <input type="file" accept=".p7s,application/pkcs7-signature" required onChange={(event) => setSignature(event.target.files && event.target.files[0])} />
          </label>
          <div className="actions">
            <button type="submit" disabled={!file || !signature || busy}>{busy ? 'Verifying…' : 'Verify'}</button>
            <Link className="button ghost" to="/login">Sign in</Link>
          </div>
        </form>
        {error && <p className="error">{error}</p>}
        {report && (
          <>
            <SignatureReport report={report} />
            <p className={`notice ${report.professional_registered ? 'ok' : 'warn'}`} data-testid="professional-match">
              {report.professional_registered
                ? `Signer registered in the network: ${report.professional_name} (${report.professional_specialty})`
                : 'The signer is not a professional of the network'}
            </p>
          </>
        )}
      </section>
    </main>
  )
}
