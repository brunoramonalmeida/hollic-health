import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppointmentCard } from '../components/AppointmentCard'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'

export function AppointmentsPage() {
  const { appointments, loadingAppointments, refreshAppointments } = useAuth()
  const navigate = useNavigate()
  const [tab, setTab] = useState<'open' | 'history'>('open')

  const list = useMemo(() => {
    if (tab === 'open') {
      return appointments.filter((a) => a.status === 'marked' || a.status === 'under_analysis')
    }
    return appointments.filter((a) => a.status === 'completed' || a.status === 'canceled')
  }, [appointments, tab])

  return (
    <div className="page appointments">
      <h1 className="title-page">Agendamentos</h1>
      <div className="toggle">
        <button
          type="button"
          className={tab === 'open' ? 'is-active' : ''}
          onClick={() => setTab('open')}
        >
          Em aberto
        </button>
        <button
          type="button"
          className={tab === 'history' ? 'is-active' : ''}
          onClick={() => setTab('history')}
        >
          Histórico
        </button>
      </div>
      <Button onClick={() => navigate('/app/agendar')}>Agendar consulta</Button>
      {loadingAppointments ? (
        <div className="empty-state">
          <div className="spinner" />
        </div>
      ) : list.length === 0 ? (
        <div className="empty-state">
          Não há conteúdo disponível
          <button
            type="button"
            onClick={() => void refreshAppointments()}
            style={{
              marginTop: 12,
              background: 'none',
              border: 'none',
              color: 'var(--primary)',
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            Atualizar
          </button>
        </div>
      ) : (
        list.map((a) => <AppointmentCard key={a.id} appointment={a} />)
      )}
    </div>
  )
}
