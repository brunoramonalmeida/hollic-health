import { useEffect, useState } from 'react'
import { api } from '../api'
import PageHeader from '../components/PageHeader'
import { useSession } from '../session'

const emptyPassword = { current_password: '', new_password: '', confirm: '' }

/**
 * The seller's account: its contact data (the name, the region and the
 * status are managed by the staff) and its password.
 */
export default function MyAccount() {
  const session = useSession()
  const [me, setMe] = useState(null)
  const [form, setForm] = useState({ document: '', email: '', phone: '' })
  const [password, setPassword] = useState(emptyPassword)
  const [error, setError] = useState('')
  const [passwordError, setPasswordError] = useState('')
  const [saving, setSaving] = useState(false)

  function load() {
    return api('/sellers/me')
      .then((row) => {
        setMe(row)
        setForm({ document: row.document, email: row.email, phone: row.phone })
      })
      .catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
  }, [])

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api('/sellers/me', { method: 'PUT', body: form, message: 'Contact data updated' })
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function changePassword(event) {
    event.preventDefault()
    setPasswordError('')
    if (password.new_password !== password.confirm) {
      setPasswordError('The confirmation does not match the new password.')
      return
    }
    setSaving(true)
    try {
      await api('/auth/password', {
        method: 'PUT',
        body: { current_password: password.current_password, new_password: password.new_password },
        message: 'Password changed',
      })
      setPassword(emptyPassword)
    } catch (err) {
      setPasswordError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (error && !me) return <p className="error">{error}</p>
  if (!me) return <p className="muted">Loading…</p>

  return (
    <section>
      <PageHeader crumb="My area / Account" title="My account" hint="Your contact data and your password. Your name, region and status are managed by the staff." />
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          <h2>Contact data</h2>
          <dl>
            <dt>Name</dt>
            <dd>{me.name}</dd>
            <dt>Login</dt>
            <dd>{session.user.email}</dd>
            <dt>Region</dt>
            <dd>{me.region_name} <small className="muted">· {me.commission_bp / 100}% commission</small></dd>
            <dt>Status</dt>
            <dd><span className={`badge ${me.active ? 'ativa' : 'vencida'}`}>{me.active ? 'Active' : 'Inactive'}</span></dd>
          </dl>
          <label>
            Document (CPF/CNPJ)
            <input value={form.document} maxLength={20} onChange={(event) => setForm({ ...form, document: event.target.value })} />
          </label>
          <label>
            Contact email
            <input type="email" value={form.email} maxLength={180} onChange={(event) => setForm({ ...form, email: event.target.value })} />
            <small className="hint">Where your sale and commission notices are sent (besides your login).</small>
          </label>
          <label>
            Phone
            <input value={form.phone} maxLength={30} onChange={(event) => setForm({ ...form, phone: event.target.value })} />
          </label>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            <button type="submit" disabled={saving}>Save</button>
          </div>
        </form>
        <form className="panel" onSubmit={changePassword}>
          <h2>Password</h2>
          <label>
            Current password *
            <input type="password" value={password.current_password} required autoComplete="current-password" onChange={(event) => setPassword({ ...password, current_password: event.target.value })} />
          </label>
          <label>
            New password *
            <input type="password" value={password.new_password} required minLength={6} autoComplete="new-password" onChange={(event) => setPassword({ ...password, new_password: event.target.value })} />
            <small className="hint">At least 6 characters.</small>
          </label>
          <label>
            Confirm the new password *
            <input type="password" value={password.confirm} required minLength={6} autoComplete="new-password" onChange={(event) => setPassword({ ...password, confirm: event.target.value })} />
          </label>
          {passwordError && <p className="error">{passwordError}</p>}
          <div className="actions">
            <button type="submit" disabled={saving}>Change password</button>
          </div>
        </form>
      </div>
    </section>
  )
}
