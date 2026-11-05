import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { endpoints } from '../api/endpoints'
import { fetchHighlights } from '../api/services'
import type { ApiHighlight } from '../api/types'
import { Button } from '../components/Button'
import { usePwaInstall } from '../hooks/usePwaInstall'
import { WHATSAPP, WHATSAPP_LABEL } from '../data/mock'

export function HomePage() {
  const navigate = useNavigate()
  const { showInstallUi } = usePwaInstall()
  const [highlights, setHighlights] = useState<ApiHighlight[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    fetchHighlights()
      .then((data) => {
        if (active) setHighlights(Array.isArray(data) ? data : [])
      })
      .catch(() => {
        if (active) setHighlights([])
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <div className="page home">
      <div className="home__intro">
        <img src="/assets/icons/card.svg" alt="" />
        <p>
          <span className="text-destak">Tudo que você precisa em um só lugar. </span>
          Consultas e Exames com até 70% de descontos em até 3x no cartão*
        </p>
      </div>

      {!loading && highlights.length > 0 && (
        <div className="carousel">
          {highlights.map((b) => {
            const imageUrl = b.url || endpoints.highlightImage(b.id)
            return (
              <article
                key={b.id}
                className="carousel__item"
                style={{
                  backgroundImage: `linear-gradient(135deg, rgba(10,24,61,.75), rgba(26,58,122,.65)), url(${imageUrl})`,
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                }}
              >
                <h3>{b.name}</h3>
              </article>
            )
          })}
        </div>
      )}

      <div className="menu-grid">
        <button
          type="button"
          className="menu-card menu-card--primary"
          onClick={() => navigate('/app/agendar')}
        >
          <img
            src="/assets/icons/appointments_icon_filled.svg"
            alt=""
            style={{
              filter:
                'brightness(0) saturate(100%) invert(8%) sepia(28%) saturate(1800%) hue-rotate(192deg)',
            }}
          />
          <span>Agende{'\n'}sua consulta</span>
        </button>
        <button
          type="button"
          className="menu-card menu-card--secondary"
          onClick={() => navigate('/app/rede')}
        >
          <img src="/assets/images/accredited_network.svg" alt="" />
          <span>Rede{'\n'}credenciada</span>
        </button>
        <button
          type="button"
          className="menu-card menu-card--secondary"
          onClick={() => navigate('/app/parceiros')}
        >
          <img src="/assets/images/partners.svg" alt="" />
          <span>Nossos{'\n'}parceiros</span>
        </button>
      </div>

      <p className="home__info">
        Agendamento ágil e prático de
        <br /> diversas especialidades médicas.
      </p>

      {showInstallUi && (
        <div style={{ padding: '10px 20px 0' }}>
          <Button variant="icon" onClick={() => navigate('/app/instalar')}>
            <img
              src="/assets/icons/icon-192.png"
              alt=""
              style={{ width: 22, height: 22, borderRadius: 4 }}
            />
            Adicionar à tela inicial
          </Button>
        </div>
      )}

      <div style={{ padding: '10px 20px' }}>
        <Button
          variant="icon"
          onClick={() => window.open(`https://wa.me/${WHATSAPP}`, '_blank')}
        >
          <img src="/assets/images/icon_phone.svg" alt="" />
          Atendimento: {WHATSAPP_LABEL}
        </Button>
      </div>

      <p className="home__about">
        A <strong>Santé</strong>, tem como objetivo a inclusão social de todos, oferecendo
        assistência à saúde de qualidade aos seus colaboradores por um preço acessível.
        <strong>
          {' '}
          Preservar a saúde e a qualidade de vida das pessoas, a partir de um atendimento
          diferenciado, profissional, ético, humano e personalizado.
        </strong>
      </p>
    </div>
  )
}
