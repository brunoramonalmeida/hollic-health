import { useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'

export function WelcomePage() {
  const navigate = useNavigate()

  return (
    <div className="page page--white welcome">
      <div className="welcome__logo">
        <img src="/assets/images/logo.svg" alt="Santé Saúde" />
      </div>
      <div className="welcome__body">
        <img src="/assets/images/vector_apresentation.svg" alt="" />
        <p>
          Assine nosso plano e fique à vontade
          <br /> para cuidar de sua saúde e de seus
          <br /> familiares. <strong>Vem pra Santé!</strong>
        </p>
      </div>
      <div className="welcome__actions">
        <Button onClick={() => navigate('/login')}>Fazer Login</Button>
      </div>
    </div>
  )
}
