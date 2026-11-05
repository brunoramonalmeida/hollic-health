import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useSession } from '../session'

export default function Login() {
  const navigate = useNavigate()
  const session = useSession()
  const [email, setEmail] = useState('foo.bar@example.com')
  const [password, setPassword] = useState('saude123')
  // Why the server signed us out (e.g. a seller access deactivated), if it
  // did; kept until the next sign-in attempt (the page may load twice).
  const [error, setError] = useState(() => sessionStorage.getItem('signedOutReason') || '')

  async function submit(event) {
    event.preventDefault()
    setError('')
    sessionStorage.removeItem('signedOutReason')
    try {
      const data = await api('/auth/token', { method: 'POST', body: { email, password }, token: '', message: false })
      localStorage.setItem('token', data.access_token)
      await session.refresh()
      navigate('/')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <main className="login">
      <section className="login-brand">
        <div className="brand-block">
          <span className="brand-mark">S</span>
          <p className="brand-name">Santé Saúde<small>Operations</small></p>
        </div>
        <h2>Clinic operations</h2>
        <p>Appointments, customers, plans and the professional schedule for the Santé Saúde network.</p>
      </section>
      <section className="login-panel">
        <form onSubmit={submit}>
          <h1>Sign in</h1>
          <p className="muted">Use your staff, client, professional or seller account to open the panel.</p>
          <label>
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">Sign in</button>
          <p className="hint">Pharmacy? <Link to="/verify-prescription">Verify a prescription</Link> without signing in.</p>
          <p className="hint">Demo accounts (password saude123): foo.bar@example.com (admin), supervisor@example.com, attendant@example.com, foo.holder@example.com (client), lorem.ipsum@example.com (professional), ana.vendas@example.com (seller, coordinator), bruno.vendas@example.com (seller)</p>
        </form>
      </section>
    </main>
  )
}
