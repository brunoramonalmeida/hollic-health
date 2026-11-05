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
      setError('Informe um e-mail válido.')
      return
    }
    if (!password) {
      setError('Informe a senha.')
      return
    }
    setLoading(true)
    const result = await login(email, password)
    setLoading(false)
    if (result.ok) {
      navigate('/app', { replace: true })
    } else {
      setError(result.message ?? 'Falha no login.')
    }
  }

  return (
    <div className="page page--gradient">
      <div className="login">
        <img className="login__bg" src="/assets/images/login_background.png" alt="" />
        <div className="login__content">
          <img className="login__logo" src="/assets/images/logo_splash.svg" alt="Santé Saúde" />
          <p className="login__hint">
            Entre com seu e-mail e senha
            <br /> para acessar sua conta
          </p>
          <div style={{ width: '100%' }}>
            <div className="form-field">
              <label htmlFor="email">E-mail</label>
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
              <label htmlFor="password">Senha</label>
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
                aria-label={showPassword ? 'Ocultar senha' : 'Mostrar senha'}
              >
                <img src="/assets/icons/password_secundary_icon.svg" alt="" />
              </button>
            </div>
            {error && <p className="form-error">{error}</p>}
            <div style={{ marginTop: 16 }}>
              <Button type="button" variant="third" loading={loading} onClick={() => void handleLogin()}>
                Entrar
              </Button>
            </div>
          </div>
          {!loading && (
            <Link to="/recuperar" className="login__recover">
              Recuperar senha
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
