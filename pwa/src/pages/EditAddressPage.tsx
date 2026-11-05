import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { endpoints } from '../api/endpoints'
import { toAddressPayload } from '../api/mappers'
import { createAddressRequest, updateAddressRequest } from '../api/services'
import { BackHeader } from '../components/BackHeader'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'

interface CityOption {
  id: number
  name: string
  state_id?: number
  state?: { id: number; name: string; uf?: string }
}

export function EditAddressPage() {
  const { address, updateAddress, refreshAddresses } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ ...address })
  const [cities, setCities] = useState<CityOption[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingCities, setLoadingCities] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api
      .get<CityOption[]>(endpoints.cities)
      .then((data) => {
        if (active) setCities(Array.isArray(data) ? data : [])
      })
      .catch(() => {
        if (active) setCities([])
      })
      .finally(() => {
        if (active) setLoadingCities(false)
      })
    return () => {
      active = false
    }
  }, [])

  const sortedCities = useMemo(
    () => [...cities].sort((a, b) => a.name.localeCompare(b.name, 'pt-BR')),
    [cities],
  )

  function onCityChange(cityId: number) {
    const city = cities.find((c) => c.id === cityId)
    if (!city) return
    setForm((f) => ({
      ...f,
      cityId: city.id,
      city: city.name,
      stateId: city.state?.id ?? city.state_id,
      state: city.state?.uf || city.state?.name || '',
    }))
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const payload = toAddressPayload(form)
      if (form.id) {
        await updateAddressRequest(form.id, payload)
      } else {
        await createAddressRequest(payload)
      }
      updateAddress(form)
      await refreshAddresses()
      navigate('/app/perfil')
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'Falha ao salvar endereço')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <BackHeader title="Endereço" />
      <form className="edit-form form-stack" onSubmit={handleSubmit}>
        <div className="form-field form-field--dark">
          <label htmlFor="zipCode">CEP</label>
          <input
            id="zipCode"
            value={form.zipCode}
            onChange={(e) => setForm((f) => ({ ...f, zipCode: e.target.value }))}
            required
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="street">Rua</label>
          <input
            id="street"
            value={form.street}
            onChange={(e) => setForm((f) => ({ ...f, street: e.target.value }))}
            required
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="number">Número</label>
          <input
            id="number"
            value={form.number}
            onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
            required
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="complement">Complemento</label>
          <input
            id="complement"
            value={form.complement}
            onChange={(e) => setForm((f) => ({ ...f, complement: e.target.value }))}
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="neighborhood">Bairro</label>
          <input
            id="neighborhood"
            value={form.neighborhood}
            onChange={(e) => setForm((f) => ({ ...f, neighborhood: e.target.value }))}
            required
          />
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="cityId">Cidade</label>
          <select
            id="cityId"
            value={form.cityId || ''}
            onChange={(e) => onCityChange(Number(e.target.value))}
            required
            disabled={loadingCities}
          >
            <option value="">{loadingCities ? 'Carregando...' : 'Selecione'}</option>
            {sortedCities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {c.state?.uf ? `/${c.state.uf}` : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field form-field--dark">
          <label htmlFor="state">UF</label>
          <input id="state" value={form.state} disabled />
        </div>
        {error && <p className="form-error">{error}</p>}
        <Button type="submit" loading={loading}>
          Salvar endereço
        </Button>
      </form>
    </div>
  )
}
