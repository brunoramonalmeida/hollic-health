import { useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'

export function WelcomePage() {
  const navigate = useNavigate()

  return (
    <div className="page page--white welcome">
      <div className="welcome__logo">
        <p className="welcome__name">Hollic Health</p>
      </div>
      <div className="welcome__body">
        <img src="/assets/images/vector_apresentation.svg" alt="" />
        <p>
          Book your appointment and take care
          <br /> of your health and your
          <br /> family. <strong>Come to Hollic!</strong>
        </p>
      </div>
      <div className="welcome__actions">
        <Button onClick={() => navigate('/login')}>Log in</Button>
      </div>
    </div>
  )
}
