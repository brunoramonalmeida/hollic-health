import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleLogin() {
    setError('')
    if (!email.includes('@')) {
      setError('Enter a valid email.')
      return
    }
    if (!password) {
      setError('Enter your password.')
      return
    }
    setLoading(true)
    const result = await login(email, password)
    setLoading(false)
    if (result.ok) {
      navigate('/app', { replace: true })
    } else {
      setError(result.message ?? 'Sign-in failed.')
    }
  }

  return (
    <div className="page page--gradient">
      <div className="login">
        <img className="login__bg" src="/assets/images/login_background.png" alt="" />
        <div className="login__content">
          <p className="login__logo">Hollic Health</p>
          <p className="login__hint">
            Enter your email and password
            <br /> to open your account
          </p>
          <div style={{ width: '100%' }}>
            <div className="form-field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleLogin()
                }}
              />
              <img className="field-icon" src="/assets/icons/email_secundary_icon.svg" alt="" />
            </div>
            <div className="form-field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleLogin()
                }}
              />
              <button
                type="button"
                className="field-icon"
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                <img src="/assets/icons/password_secundary_icon.svg" alt="" />
              </button>
            </div>
            {error && <p className="form-error">{error}</p>}
            <div style={{ marginTop: 16 }}>
              <Button type="button" variant="third" loading={loading} onClick={() => void handleLogin()}>
                Sign in
              </Button>
            </div>
          </div>
          {!loading && (
            <Link to="/recover" className="login__recover">
              Forgot password
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
