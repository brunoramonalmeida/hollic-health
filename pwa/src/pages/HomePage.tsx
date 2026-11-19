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
          <span className="text-destak">Everything you need in one place. </span>
          Appointments and tests with up to 70% off, in up to 3 card installments*
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
          onClick={() => navigate('/app/schedule')}
        >
          <img
            src="/assets/icons/appointments_icon_filled.svg"
            alt=""
            style={{
              filter:
                'brightness(0) saturate(100%) invert(8%) sepia(28%) saturate(1800%) hue-rotate(192deg)',
            }}
          />
          <span>Book{'\n'}an appointment</span>
        </button>
        <button
          type="button"
          className="menu-card menu-card--secondary"
          onClick={() => navigate('/app/network')}
        >
          <img src="/assets/images/accredited_network.svg" alt="" />
          <span>Accredited{'\n'}network</span>
        </button>
        <button
          type="button"
          className="menu-card menu-card--secondary"
          onClick={() => navigate('/app/partners')}
        >
          <img src="/assets/images/partners.svg" alt="" />
          <span>Our{'\n'}partners</span>
        </button>
      </div>

      <p className="home__info">
        Quick, simple booking across
        <br /> many medical specialties.
      </p>

      {showInstallUi && (
        <div style={{ padding: '10px 20px 0' }}>
          <Button variant="icon" onClick={() => navigate('/app/install')}>
            Add to home screen
          </Button>
        </div>
      )}

      <div style={{ padding: '10px 20px' }}>
        <Button
          variant="icon"
          onClick={() => window.open(`https://wa.me/${WHATSAPP}`, '_blank')}
        >
          <img src="/assets/images/icon_phone.svg" alt="" />
          Support: {WHATSAPP_LABEL}
        </Button>
      </div>

      <p className="home__about">
        <strong>Hollic Health</strong> aims to include everyone, offering
        quality healthcare to its members at an affordable price.
        <strong>
          {' '}
          We look after people’s health and quality of life with care that is
          professional, ethical, human and personal.
        </strong>
      </p>
    </div>
  )
}
