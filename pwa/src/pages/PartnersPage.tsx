import { useEffect, useState } from 'react'
import { endpoints } from '../api/endpoints'
import { fetchPartners } from '../api/services'
import type { ApiPartner } from '../api/types'
import { BackHeader } from '../components/BackHeader'

export function PartnersPage() {
  const [partners, setPartners] = useState<ApiPartner[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    fetchPartners()
      .then((data) => {
        if (active) setPartners(Array.isArray(data) ? data : [])
      })
      .catch((err) => {
        if (active) setError(err?.message || 'Falha ao carregar parceiros')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <div className="page">
      <BackHeader title="Nossos parceiros" />
      <div className="list-page">
        {loading && (
          <div className="empty-state">
            <div className="spinner" />
          </div>
        )}
        {error && <div className="empty-state">{error}</div>}
        {!loading &&
          !error &&
          partners.map((p) => (
            <article key={p.id} className="list-card" style={{ display: 'flex', gap: 12 }}>
              <img
                src={p.url || endpoints.partnerImage(p.id)}
                alt=""
                style={{ width: 56, height: 56, objectFit: 'contain', borderRadius: 8 }}
                onError={(e) => {
                  ;(e.target as HTMLImageElement).style.display = 'none'
                }}
              />
              <div>
                <h3>{p.name}</h3>
              </div>
            </article>
          ))}
        {!loading && !error && partners.length === 0 && (
          <div className="empty-state">Nenhum parceiro encontrado</div>
        )}
      </div>
    </div>
  )
}
