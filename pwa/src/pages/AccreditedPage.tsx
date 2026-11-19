import { useEffect, useMemo, useState } from 'react'
import { fetchAccreditedNetwork } from '../api/services'
import { BackHeader } from '../components/BackHeader'

interface FlatClinic {
  id: number
  name: string
  city: string
  state: string
  specialties: string[]
}

interface NetworkState {
  id: number
  name: string
  cities: Array<{
    id: number
    name: string
    clinics: Array<{
      id: number
      name: string
      specialties: Array<{ id: number; name: string }>
    }>
  }>
}

function flattenNetwork(states: NetworkState[]): FlatClinic[] {
  const list: FlatClinic[] = []
  for (const state of states) {
    for (const city of state.cities || []) {
      for (const clinic of city.clinics || []) {
        list.push({
          id: clinic.id,
          name: clinic.name,
          city: city.name,
          state: state.name,
          specialties: (clinic.specialties || []).map((s) => s.name),
        })
      }
    }
  }
  return list
}

export function AccreditedPage() {
  const [states, setStates] = useState<NetworkState[]>([])
  const [stateFilter, setStateFilter] = useState('ALL')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    fetchAccreditedNetwork()
      .then((data) => {
        if (active) setStates(Array.isArray(data) ? (data as NetworkState[]) : [])
      })
      .catch((err) => {
        if (active) setError(err?.message || 'Could not load the accredited network')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const clinics = useMemo(() => flattenNetwork(states), [states])

  const list = useMemo(() => {
    return clinics.filter((a) => {
      const matchState = stateFilter === 'ALL' || a.state === stateFilter
      const q = query.trim().toLowerCase()
      const matchQuery =
        !q ||
        a.name.toLowerCase().includes(q) ||
        a.city.toLowerCase().includes(q) ||
        a.specialties.some((s) => s.toLowerCase().includes(q))
      return matchState && matchQuery
    })
  }, [clinics, stateFilter, query])

  const stateOptions = useMemo(
    () => Array.from(new Set(clinics.map((c) => c.state))).sort(),
    [clinics],
  )

  return (
    <div className="page">
      <BackHeader title="Accredited network" />
      <div className="list-page">
        <div className="filter-row">
          <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
            <option value="ALL">All states</option>
            {stateOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            placeholder="Search..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {loading && (
          <div className="empty-state">
            <div className="spinner" />
          </div>
        )}
        {error && <div className="empty-state">{error}</div>}
        {!loading &&
          !error &&
          list.map((a) => (
            <article key={`${a.id}-${a.city}`} className="list-card">
              <h3>{a.name}</h3>
              <p>{a.specialties.slice(0, 4).join(' · ') || 'Specialties'}</p>
              <p>
                {a.city}/{a.state}
              </p>
            </article>
          ))}
        {!loading && !error && list.length === 0 && (
          <div className="empty-state">No accredited clinics found</div>
        )}
      </div>
    </div>
  )
}
